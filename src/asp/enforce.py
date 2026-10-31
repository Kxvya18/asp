"""Enforce mode: block any AWS call that a policy does not allow, before it is sent.

    from asp import enforce

    with enforce("policy.json"):     # e.g. the policy asp recorded for this agent
        agent.run(task)              # a call outside the policy raises asp.AccessDenied

Recording tells you what code needs. Enforcing makes sure it never does more: if an AI agent
is misled (for example by instructions hidden in a file it reads) into deleting data or
touching another bucket, the call is stopped inside your process and never reaches AWS.

How a call is judged, like IAM does it, kept simple:
  * the call's IAM actions come from mappings.py and its resources (ARNs) from resources.py,
    plus the permissions AWS checks behind the scenes (implicit.py: iam:PassRole, KMS keys);
  * a call is allowed when every one of those actions, on every resource, is matched by an
    Allow statement and by no Deny statement (Deny always wins);
  * Action / NotAction and Resource / NotResource are understood, with "*" and "?" wildcards.
    Action names ignore upper/lower case, ARNs do not;
  * when asp cannot tell which resource a call touches (services it does not model yet),
    an Allow that names the action is enough, and a Deny that names the action still blocks;
  * Condition blocks are not evaluated: a statement with a Condition is treated as if the
    condition were true. Enforce mode is a guard rail in your process, not a replacement for
    the role's real IAM policy, which AWS still checks.

on_violation="raise" (default) blocks the call with asp.AccessDenied, a botocore ClientError
with the code "AccessDenied", so code that already handles AWS errors handles it the same
way. on_violation="log" lets the call through and only logs it: a safe way to try a policy
on real traffic before switching to "raise". Either way every violation is kept in
`enforcer.violations`.
"""

from __future__ import annotations

import logging
from contextlib import ContextDecorator
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from botocore.exceptions import ClientError

from asp import recorder
from asp.check import load_policy
from asp.implicit import implied
from asp.mappings import iam_actions
from asp.resources import S3_OBJECT_ACTIONS, resources

log = logging.getLogger("asp")

MODES = ("raise", "log")


class AccessDenied(ClientError):
    """Raised by enforce mode for a call the enforced policy does not allow.

    It is a botocore ClientError with the error code "AccessDenied", exactly like the error
    AWS returns, so `except ClientError` handles it too."""

    def __init__(self, violation: Violation):
        self.violation = violation
        super().__init__(
            {"Error": {"Code": "AccessDenied", "Message": violation.message}},
            violation.operation,
        )


@dataclass(frozen=True)
class Violation:
    """One blocked (or, in "log" mode, flagged) call."""

    action: str  # the IAM action that was not allowed, e.g. "s3:DeleteBucket"
    resource: str  # the ARN it was used on, or "*" when asp cannot tell
    service: str  # the API call, e.g. "s3" ...
    operation: str  # ... and "DeleteBucket"
    reason: str  # "denied" (a Deny statement matched) or "not allowed" (no Allow matched)

    @property
    def message(self) -> str:
        on = "" if self.resource == "*" else f" on {self.resource}"
        return f"asp enforce: {self.action}{on} is {self.reason} by the enforced policy"


class Enforcer(ContextDecorator):
    """Use as `with enforce(policy):` or as a decorator `@enforce("policy.json")`.

    Like recording, enforcing is process-wide: calls from every thread are checked while
    an enforcer is active. Nested enforcers all apply, so a call must pass every one of them.
    """

    def __init__(self, policy, on_violation: str = "raise"):
        if on_violation not in MODES:
            raise ValueError(f"on_violation must be one of {MODES}, not {on_violation!r}")
        if isinstance(policy, recorder.Recorder):  # enforce exactly what was recorded
            policy = policy.policy()
        self.policy = load_policy(policy)
        self.on_violation = on_violation
        self.violations: list[Violation] = []
        self._statements = _statements(self.policy)

    def __enter__(self) -> Enforcer:
        with recorder._lock:
            recorder._install()
            recorder._enforcers.append(self)
        return self

    def __exit__(self, *exc) -> bool:
        with recorder._lock:
            recorder._enforcers.remove(self)
            recorder._uninstall()
        return False  # never hide an exception from the enforced code

    def allows(self, action: str, resource: str = "*") -> bool:
        """True when the policy allows `action` on `resource` (an ARN, or "*" if unknown)."""
        return _decide(self._statements, action, resource) is None

    def _inspect(self, call) -> None:
        """Called by the botocore wrapper for every call, before it is sent."""
        for action, resource in _needs(call):
            reason = _decide(self._statements, action, resource)
            if reason is None:
                continue
            violation = Violation(action, resource, call.service, call.operation, reason)
            self.violations.append(violation)
            log.warning(violation.message)
            if self.on_violation == "raise":
                raise AccessDenied(violation)

    def summary(self) -> str:
        """The violations in plain English, one per line."""
        if not self.violations:
            return "no violations"
        verb = "blocked" if self.on_violation == "raise" else "would have been blocked"
        lines = [f"{len(self.violations)} call(s) {verb}:"]
        lines += [f"  - {v.service}.{v.operation}: {v.message}" for v in self.violations]
        return "\n".join(lines)


def enforce(policy: dict | str | Path | recorder.Recorder, on_violation: str = "raise"):
    """Block AWS calls the policy does not allow. See Enforcer.

    policy: a policy dict, JSON text, a path to a JSON file, or a Recorder (its policy).
    on_violation: "raise" to block (default) or "log" to only log violations."""
    return Enforcer(policy, on_violation=on_violation)


# ---- what a call needs ----------------------------------------------------------------------


def _needs(call) -> list[tuple[str, str]]:
    """(IAM action, resource ARN) pairs one call needs, including implicit permissions that
    are visible before the call is sent (iam:PassRole, KMS keys named in the request)."""
    pairs = []
    for service, operation, params in [(call.service, call.operation, call.params)] + list(
        implied(call.service, call.operation, call.params, None)
    ):
        for action in iam_actions(service, operation, params):
            for arn in _arns(action, params, call.region):
                pairs.append((action, arn))
    return pairs


def _arns(action: str, params: dict, region: str | None) -> list[str]:
    """Like resources.py, but for S3 objects name the exact keys when the call shows them,
    so a policy limited to a prefix (bucket/reports/*) is enforced precisely."""
    service, name = action.split(":", 1)
    if service == "s3" and name in S3_OBJECT_ACTIONS:
        bucket = params.get("Bucket")
        keys = [params["Key"]] if "Key" in params else []
        keys += [o["Key"] for o in params.get("Delete", {}).get("Objects", []) if "Key" in o]
        if name == "GetObject" and "CopySource" in params:  # reading the source of a copy
            bucket, keys = _copy_source(params["CopySource"])
        if bucket and keys:
            return [f"arn:aws:s3:::{bucket}/{key}" for key in keys]
    return resources(action, params, region)


def _copy_source(source) -> tuple[str | None, list[str]]:
    if isinstance(source, dict):
        return source.get("Bucket"), [source["Key"]] if "Key" in source else []
    bucket, _, key = str(source).lstrip("/").partition("/")  # "bucket/key"
    return bucket, [key.split("?", 1)[0]] if key else []


# ---- how a policy decides -------------------------------------------------------------------


def _as_list(value) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def _statements(policy: dict) -> list[dict]:
    statements = policy.get("Statement", [])
    return [statements] if isinstance(statements, dict) else list(statements)


def _action_matches(statement: dict, action: str) -> bool:
    if "Action" in statement:
        return any(fnmatchcase(action.lower(), p.lower()) for p in _as_list(statement["Action"]))
    if "NotAction" in statement:
        return not any(
            fnmatchcase(action.lower(), p.lower()) for p in _as_list(statement["NotAction"])
        )
    return False


def _resource_matches(statement: dict, resource: str) -> bool:
    """Does the statement cover this resource? When asp cannot tell the resource ("*"), the
    statement is taken to cover it: an Allow naming the action allows, a Deny naming it
    blocks (see the module docstring)."""
    if resource == "*":
        return True
    if "Resource" in statement:
        return any(_arn_matches(resource, p) for p in _as_list(statement["Resource"]))
    if "NotResource" in statement:
        return not any(_arn_matches(resource, p) for p in _as_list(statement["NotResource"]))
    return True  # no resource element: applies to every resource


def _arn_matches(arn: str, pattern: str) -> bool:
    """Match an ARN against a policy pattern. An ARN field asp could not see (the account id,
    or the region) is written "*" and matches whatever the pattern has there."""
    if pattern == "*" or fnmatchcase(arn, pattern):
        return True
    a, p = arn.split(":", 5), pattern.split(":", 5)
    if len(a) != 6 or len(p) != 6:
        return False
    return all(x == "*" or fnmatchcase(x, y) for x, y in zip(a, p, strict=True))


def _decide(statements: list[dict], action: str, resource: str) -> str | None:
    """None when allowed; otherwise the reason: "denied" or "not allowed"."""

    def covers(st: dict) -> bool:
        return _action_matches(st, action) and _resource_matches(st, resource)

    if any(st.get("Effect") == "Deny" and covers(st) for st in statements):
        return "denied"
    if any(st.get("Effect") == "Allow" and covers(st) for st in statements):
        return None
    return "not allowed"
