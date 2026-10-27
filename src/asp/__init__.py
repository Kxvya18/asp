"""asp - Agent Scoped Permissions.

Record the AWS calls your code (or AI agent) makes and get the least-privilege IAM policy.

    from asp import record

    with record() as rec:
        run_my_script()
    print(rec.policy_json())

And make sure it never does more:

    from asp import enforce

    with enforce(rec):            # or a policy dict / JSON / file
        run_my_script()           # a call outside the policy raises asp.AccessDenied
"""

from asp.check import CheckResult, check
from asp.enforce import AccessDenied, Enforcer, Violation, enforce
from asp.recorder import Call, Recorder, record

__all__ = [
    "AccessDenied",
    "Call",
    "CheckResult",
    "Enforcer",
    "Recorder",
    "Violation",
    "check",
    "enforce",
    "record",
]
__version__ = "0.1.1"
