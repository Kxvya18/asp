"""Record every AWS call made through boto3 and turn the calls into an IAM policy.

How it works: every boto3 client sends its requests through one method,
botocore's BaseClient._make_api_call. While a recorder is active we wrap that method,
write down which service and operation were called, then let the call continue normally.
"""

from __future__ import annotations

import json
import threading
from contextlib import ContextDecorator
from dataclasses import dataclass, field
from pathlib import Path

from botocore.client import BaseClient

from asp.check import CheckResult, check
from asp.mappings import iam_actions
from asp.resources import resources

_original_make_api_call = BaseClient._make_api_call
_active: list[Recorder] = []  # recorders currently recording (they can be nested)
_lock = threading.Lock()


@dataclass(frozen=True)
class Call:
    """One AWS API call: e.g. service "s3", operation "PutObject"."""

    service: str  # the IAM prefix, e.g. "s3", "dynamodb"
    operation: str  # the API operation name, e.g. "PutObject"
    region: str | None = None
    params: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def iam_actions(self) -> list[str]:
        """The IAM actions AWS checks for this call (see mappings.py)."""
        return iam_actions(self.service, self.operation, self.params)


def _recording_make_api_call(client, operation_name, api_params):
    call = Call(
        service=client.meta.service_model.signing_name,
        operation=operation_name,
        region=client.meta.region_name,
        params=dict(api_params),
    )
    # recorded before sending: a call that fails still needed the permission
    for recorder in list(_active):
        recorder.calls.append(call)
    return _original_make_api_call(client, operation_name, api_params)


class Recorder(ContextDecorator):
    """Use as `with record() as rec:` or as a decorator `@record(save_to="policy.json")`.

    Note: recording is process-wide. Calls from all threads made while a recorder
    is active are recorded.
    """

    def __init__(self, save_to: str | Path | None = None):
        self.calls: list[Call] = []
        self.save_to = save_to

    def __enter__(self) -> Recorder:
        with _lock:
            if not _active:
                BaseClient._make_api_call = _recording_make_api_call
            _active.append(self)
        return self

    def __exit__(self, *exc) -> bool:
        with _lock:
            _active.remove(self)
            if not _active:
                BaseClient._make_api_call = _original_make_api_call
        if self.save_to:
            self.save(self.save_to)
        return False  # never hide an exception from the recorded code

    def actions(self) -> list[str]:
        """The distinct IAM actions used, sorted."""
        return sorted({action for c in self.calls for action in c.iam_actions})

    def policy(self, scope_resources: bool = True) -> dict:
        """An IAM policy that allows exactly the recorded actions.

        scope_resources: name the exact buckets / tables / queues used instead of "*".
        Actions that need the same resources share one statement."""
        if not scope_resources:
            statements = [{"Effect": "Allow", "Action": self.actions(), "Resource": "*"}]
            return {"Version": "2012-10-17", "Statement": statements}

        needs: dict[str, set[str]] = {}  # action -> resources it was used on
        for call in self.calls:
            for action in call.iam_actions:
                needs.setdefault(action, set()).update(resources(action, call.params, call.region))
        for action, arns in needs.items():
            if "*" in arns:  # used on "anything" at least once
                needs[action] = {"*"}

        groups: dict[tuple[str, ...], list[str]] = {}  # resources -> actions
        for action, arns in sorted(needs.items()):
            groups.setdefault(tuple(sorted(arns)), []).append(action)
        statements = [
            {
                "Effect": "Allow",
                "Action": actions,
                "Resource": arns[0] if len(arns) == 1 else list(arns),
            }
            for arns, actions in sorted(groups.items(), key=lambda g: g[1])
        ]
        return {"Version": "2012-10-17", "Statement": statements}

    def policy_json(self, scope_resources: bool = True) -> str:
        return json.dumps(self.policy(scope_resources), indent=2)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.policy_json() + "\n")

    def check(self, existing_policy: dict | str | Path) -> CheckResult:
        """Compare a policy the role already has with what was recorded (see check.py)."""
        return check(existing_policy, self.actions())

    def terraform(self, policy_name: str = "asp-recorded") -> str:
        """The policy as a Terraform resource, ready to put in a .tf file."""
        return (
            'resource "aws_iam_policy" "asp" {\n'
            f'  name   = "{policy_name}"\n'
            "  policy = <<POLICY\n"
            f"{self.policy_json()}\n"
            "POLICY\n"
            "}\n"
        )


def record(save_to: str | Path | None = None) -> Recorder:
    """Start recording AWS calls. See Recorder."""
    return Recorder(save_to=save_to)
