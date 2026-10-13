"""Compare a policy a role already has with the actions the code really used.

Simple on purpose: this compares ACTIONS only (not resources). It understands wildcards
("s3:Get*", "*"), Deny beating Allow, and that IAM action names ignore upper/lower case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path


@dataclass
class CheckResult:
    missing: list[str]  # the code needs these, but the policy does not allow them
    unused: list[str]  # the policy allows these exact actions, but the code never used them
    wildcards: list[str]  # broad grants such as "s3:*" or "*"

    @property
    def ok(self) -> bool:
        """True when the policy allows everything the code needs."""
        return not self.missing

    def summary(self) -> str:
        lines = [f"missing (code would fail): {len(self.missing)}"]
        lines += [f"  - {a}" for a in self.missing]
        lines.append(f"unused (can be removed): {len(self.unused)}")
        lines += [f"  - {a}" for a in self.unused]
        lines.append(f"too broad (wildcards): {len(self.wildcards)}")
        lines += [f"  - {a}" for a in self.wildcards]
        return "\n".join(lines)


def load_policy(policy: dict | str | Path) -> dict:
    """Accept a policy as a dict, a JSON string or a path to a JSON file."""
    if isinstance(policy, dict):
        return policy
    text = str(policy)
    if text.lstrip().startswith("{"):
        return json.loads(text)
    return json.loads(Path(policy).read_text())


def _patterns(policy: dict, effect: str) -> list[str]:
    statements = policy.get("Statement", [])
    if isinstance(statements, dict):
        statements = [statements]
    out = []
    for st in statements:
        if st.get("Effect") == effect and "Action" in st:
            actions = st["Action"]
            out += [actions] if isinstance(actions, str) else list(actions)
    return out


def _matches(action: str, patterns: list[str]) -> bool:
    return any(fnmatchcase(action.lower(), p.lower()) for p in patterns)


def check(existing_policy: dict | str | Path, needed_actions: list[str]) -> CheckResult:
    policy = load_policy(existing_policy)
    allow, deny = _patterns(policy, "Allow"), _patterns(policy, "Deny")
    needed = {a.lower() for a in needed_actions}
    missing = [a for a in needed_actions if not _matches(a, allow) or _matches(a, deny)]
    unused = [p for p in allow if "*" not in p and p.lower() not in needed]
    wildcards = [p for p in allow if "*" in p]
    return CheckResult(sorted(set(missing)), sorted(set(unused)), sorted(set(wildcards)))
