import subprocess
import sys
from pathlib import Path

from asp.explain import explain


def test_plain_english_lines():
    policy = {
        "Statement": [
            {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::reports/*"},
            {
                "Effect": "Allow",
                "Action": ["dynamodb:Query"],
                "Resource": [
                    "arn:aws:dynamodb:us-east-1:*:table/jobs",
                    "arn:aws:dynamodb:us-east-1:*:table/jobs/index/by_site",
                ],
            },
            {"Effect": "Allow", "Action": ["bedrock:InvokeModel"], "Resource": "*"},
        ]
    }
    assert explain(policy).splitlines() == [
        "- read files in bucket 'reports'",
        "- query in table 'jobs', table 'jobs' (index 'by_site')",
        "- call AI models in ANY resource",
    ]


def test_unknown_actions_fall_back_to_their_name():
    p = {"Statement": [{"Effect": "Allow", "Action": "ec2:StopInstances", "Resource": "*"}]}
    assert explain(p) == "- ec2:StopInstances in ANY resource"


def test_ai_agent_example_runs_and_is_least_privilege():
    example = Path(__file__).parents[1] / "examples" / "ai_agent.py"
    out = subprocess.run(
        [sys.executable, str(example)], capture_output=True, text=True, check=True
    ).stdout
    assert "- read files in bucket 'reports'" in out
    assert "- write files in bucket 'archive'" in out
    assert "s3:DeleteObject" not in out and '"Resource": "*"' not in out
