"""asp serve: a local AWS endpoint that records what any program does, in any language.

A Java, Go or Node backend points its AWS SDK at this endpoint (AWS_ENDPOINT_URL), runs,
and asp writes the least-privilege policy for it. Each request is recorded, then passed on
to a moto server (a fake AWS) which answers it. Recording only reads the request:

- the service comes from the request's signature (Credential=.../region/SERVICE/aws4_request)
- the operation comes from the X-Amz-Target header (JSON APIs like DynamoDB and SQS),
  the Action field (query APIs like IAM and STS), or the method and path (REST APIs like S3),
  matched against botocore's own description of each API
- the parameters (Bucket, Key, TableName, QueueUrl...) come from the path, query and body

The calls go into a normal Recorder, so the policy is exactly what record() would write.
Open http://localhost:PORT/_asp/ to watch the calls and the policy live.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import botocore.session

from asp.recorder import Call, Recorder

_session = botocore.session.get_session()
_models: dict[str, object] = {}  # signing name -> botocore service model
_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-length", "host"}
VIEW = Path(__file__).with_name("view.html")
_RESOURCE_PARAMS = (
    "Bucket",
    "TableName",
    "QueueUrl",
    "QueueName",
    "FunctionName",
    "RoleName",
    "Name",
)


def _model(signing_name: str):
    """The botocore model of the service that signs requests with this name, e.g. "s3"."""
    if not _models:
        for name in _session.get_available_services():
            model = _session.get_service_model(name)
            # several services can share a signing name; keep the one named after it
            if model.signing_name not in _models or name == model.signing_name:
                _models[model.signing_name] = model
    return _models.get(signing_name)


def _scope(headers) -> tuple[str | None, str | None]:
    """(region, service) from the SigV4 Authorization header."""
    match = re.search(
        r"Credential=[^/]+/\d+/([^/]+)/([^/]+)/aws4_request", headers.get("Authorization", "")
    )
    return (match.group(1), match.group(2)) if match else (None, None)


def _match_rest(model, method: str, path: str, query: dict, headers) -> tuple[str, dict] | None:
    """Find the REST operation whose method, path template and required inputs fit the request.
    Returns (operation, params), or None. When several fit, the most specific one wins."""
    segments = [unquote(s) for s in path.strip("/").split("/")] if path.strip("/") else []
    best, best_score = None, -1
    for name in model.operation_names:
        op = model.operation_model(name)
        http_info = op.http
        if http_info.get("method") != method:
            continue
        template, _, literal_query = http_info["requestUri"].partition("?")
        params = _match_path(template, segments)
        if params is None:
            continue
        literals = [q.split("=")[0] for q in literal_query.split("&") if q]
        if any(key not in query for key in literals):
            continue
        members = op.input_shape.members if op.input_shape else {}
        required = op.input_shape.required_members if op.input_shape else []
        score = 10 * len(literals) + sum(1 for s in template.split("/") if s and "{" not in s)
        fits = True
        for member, shape in members.items():
            where, wire = (
                shape.serialization.get("location"),
                shape.serialization.get("name", member),
            )
            if where == "uri":
                params[member] = params.pop(wire, params.get(member))
            elif where == "querystring" and wire in query:
                params[member] = query[wire]
                score += 1
            elif where == "header" and wire in headers:
                params[member] = headers[wire]
                score += 1
            elif member in required and where in ("querystring", "header"):
                fits = False  # a required input is missing: not this operation
        if fits and score > best_score:
            best, best_score = (name, {k: v for k, v in params.items() if v is not None}), score
    return best


def _match_path(template: str, segments: list[str]) -> dict | None:
    """Match "/{Bucket}/{Key+}" against ["reports", "2026", "oct.csv"] -> labels, or None."""
    parts = [p for p in template.strip("/").split("/") if p]
    labels: dict[str, str] = {}
    for i, part in enumerate(parts):
        if part.startswith("{") and part.endswith("+}"):  # greedy: takes the rest
            if i >= len(segments):
                return None
            labels[part[1:-2]] = "/".join(segments[i:])
            return labels
        if i >= len(segments):
            return None
        if part.startswith("{"):
            labels[part[1:-1]] = segments[i]
        elif part != segments[i]:
            return None
    return labels if len(parts) == len(segments) else None


def parse(method: str, url: str, headers, body: bytes) -> Call | None:
    """Turn one HTTP request from an AWS SDK into a Call, or None if it is not one."""
    region, service = _scope(headers)
    model = _model(service) if service else None
    if model is None:
        return None
    split = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(split.query, keep_blank_values=True).items()}
    protocol = model.metadata.get("protocol")
    target = headers.get("X-Amz-Target")

    if target:  # JSON APIs: "DynamoDB_20120810.PutItem"
        params = json.loads(body or b"{}")
        return Call(
            service, target.split(".")[-1], region, params if isinstance(params, dict) else {}
        )
    if protocol in ("query", "ec2"):  # form body: Action=SendMessage&QueueUrl=...
        form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
        form.update(query)
        if "Action" in form:
            return Call(service, form.pop("Action"), region, form)
        return None

    path = split.path
    host = headers.get("Host", "")
    if service == "s3" and re.match(r"^[a-z0-9.-]+\.localhost(:\d+)?$", host):
        path = "/" + host.split(".localhost")[0] + path  # virtual-hosted: bucket.localhost
    matched = _match_rest(model, method, path, query, headers)
    if matched is None:
        return None
    operation, params = matched
    if protocol == "rest-json" and body:
        try:
            data = json.loads(body)
            if isinstance(data, dict):
                params = {**data, **params}
        except ValueError:
            pass
    return Call(service, operation, region, params)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """The recording endpoint. `with Server(port=5050) as server: ...` or `server.start()`."""

    def __init__(self, port: int = 5050, host: str = "127.0.0.1"):
        from moto.server import ThreadedMotoServer  # pip install "asp-iam[serve]"

        self.recorder = Recorder()
        self.log: list[dict] = []  # what the web view shows: time, call, actions
        self._moto_port = _free_port()
        self._moto = ThreadedMotoServer(ip_address="127.0.0.1", port=self._moto_port, verbose=False)
        self._http = ThreadingHTTPServer((host, port), _handler(self))
        self.port = self._http.server_address[1]

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def start(self) -> Server:
        self._moto.start()
        conn = http.client.HTTPConnection("127.0.0.1", self._moto_port, timeout=30)
        conn.request("POST", "/moto-api/reset")  # every server starts with an empty fake AWS
        conn.getresponse().read()
        conn.close()
        threading.Thread(target=self._http.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._http.shutdown()
        self._moto.stop()

    def __enter__(self) -> Server:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def reset(self) -> None:
        """Forget what was recorded (e.g. after creating test buckets), keep the fake AWS data."""
        self.recorder.calls.clear()
        self.log.clear()

    def add(self, call: Call, status: int) -> None:
        self.recorder.calls.append(call)
        resource = next(
            (str(call.params[k]) for k in _RESOURCE_PARAMS if k in call.params),
            "",
        )
        if call.params.get("Key"):
            resource += "/" + str(call.params["Key"])
        self.log.append(
            {
                "time": time.strftime("%H:%M:%S"),
                "service": call.service,
                "operation": call.operation,
                "resource": resource,
                "actions": call.iam_actions,
                "status": status,
            }
        )


def _handler(server: Server):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # quiet: the web view is the log
            pass

        def _body(self) -> bytes:
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                data = b""
                while True:
                    size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                    if size == 0:
                        self.rfile.readline()
                        return data
                    data += self.rfile.read(size)
                    self.rfile.readline()
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        def _send(self, status: int, body: bytes, content_type: str, headers=()) -> None:
            self.send_response(status)
            for key, value in headers:
                self.send_header(key, value)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _asp(self) -> None:
            """asp's own pages: the web view, the calls, the policy, Terraform, reset."""
            if urlsplit(self.path).path == "/_asp":  # relative links need the slash
                return self._send(301, b"", "text/plain", [("Location", "/_asp/")])
            page = urlsplit(self.path).path.removeprefix("/_asp").strip("/")
            rec = server.recorder
            if page == "reset" and self.command == "POST":
                server.reset()
                self._send(200, b'{"reset": true}', "application/json")
            elif page == "calls":
                data = {
                    "calls": server.log,
                    "actions": rec.actions(),
                    "policy": rec.policy(),
                    "explain": rec.explain(),
                }
                self._send(200, json.dumps(data).encode(), "application/json")
            elif page == "policy":
                self._send(200, rec.policy_json().encode(), "application/json")
            elif page == "terraform":
                self._send(200, rec.terraform().encode(), "text/plain")
            elif page == "":
                self._send(200, VIEW.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def _forward(self) -> None:
            if self.path.startswith("/_asp"):  # bucket names cannot start with "_"
                self._body()
                return self._asp()
            body = self._body()
            call = parse(self.command, self.path, self.headers, body)
            conn = http.client.HTTPConnection("127.0.0.1", server._moto_port, timeout=60)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP_HEADERS}
            headers["Host"] = self.headers.get("Host", "localhost")
            headers["Content-Length"] = str(len(body))
            conn.request(self.command, self.path, body=body, headers=headers)
            response = conn.getresponse()
            data = response.read()
            if call is not None:  # recorded even when AWS says no: the permission was needed
                server.add(call, response.status)
            passed = [
                (k, v)
                for k, v in response.getheaders()
                if k.lower() not in _HOP_HEADERS | {"content-type"}
            ]
            self._send(
                response.status, data, response.getheader("Content-Type", "application/xml"), passed
            )
            conn.close()

        do_GET = do_PUT = do_POST = do_DELETE = do_HEAD = do_PATCH = _forward

    return Handler
