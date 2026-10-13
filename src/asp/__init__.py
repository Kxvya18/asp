"""asp - Agent Scoped Permissions.

Record the AWS calls your code (or AI agent) makes and get the least-privilege IAM policy.

    from asp import record

    with record() as rec:
        run_my_script()
    print(rec.policy_json())
"""

from asp.check import CheckResult, check
from asp.recorder import Call, Recorder, record

__all__ = ["Call", "CheckResult", "Recorder", "check", "record"]
__version__ = "0.1.0"
