"""An AI agent with AWS tools, recorded by asp. Its least-privilege policy comes out at the end.

    uv run python examples/ai_agent.py            # scripted stand-in, no API key needed
    uv run python examples/ai_agent.py --claude   # real Claude drives the agent

--claude needs `pip install anthropic` and Anthropic credentials.

AWS is faked with moto either way, so nothing touches a real account.
"""

import json
import os
import sys

import boto3
from moto import mock_aws

from asp import record

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

TASK = (
    "Summarise the October report in the 'reports' bucket in one sentence and "
    "save the summary to the 'archive' bucket as october-summary.txt."
)

# ---- the agent's tools: plain Python functions that use boto3 ---------------------------

s3 = None  # created inside mock_aws below


def list_files(bucket):
    return [o["Key"] for o in s3.list_objects_v2(Bucket=bucket).get("Contents", [])]


def read_file(bucket, key):
    return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode()


def write_file(bucket, key, text):
    s3.put_object(Bucket=bucket, Key=key, Body=text.encode())
    return "saved"


TOOLS = {"list_files": list_files, "read_file": read_file, "write_file": write_file}

TOOL_SCHEMAS = [
    {
        "name": "list_files",
        "description": "List the files in an S3 bucket.",
        "input_schema": {
            "type": "object",
            "properties": {"bucket": {"type": "string"}},
            "required": ["bucket"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file from an S3 bucket.",
        "input_schema": {
            "type": "object",
            "properties": {"bucket": {"type": "string"}, "key": {"type": "string"}},
            "required": ["bucket", "key"],
        },
    },
    {
        "name": "write_file",
        "description": "Write a text file to an S3 bucket.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string"},
                "key": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["bucket", "key", "text"],
        },
    },
]


# ---- two ways to drive the agent ----------------------------------------------------------


def run_scripted():
    """What a well-behaved agent does for TASK, without calling an LLM."""
    files = list_files("reports")
    report = read_file("reports", files[0])
    lines = report.strip().splitlines()[1:]
    write_file(
        "archive",
        "october-summary.txt",
        f"October: {len(lines)} sites shipped {sum(int(r.split(',')[1]) for r in lines)} pallets.",
    )


def run_with_claude():
    """A plain agent loop: ask Claude, run the tools it asks for, send results back."""
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": TASK}]
    while True:
        response = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=16000,
            tools=TOOL_SCHEMAS,
            messages=messages,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if the model declines, the API retries on a fallback model
        )
        if response.stop_reason == "refusal":
            sys.exit("The model declined the task.")
        if response.stop_reason != "tool_use":
            return next((b.text for b in response.content if b.type == "text"), "")
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = TOOLS[block.name](**block.input)
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(output)}
                )
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        for name in ("reports", "archive"):
            s3.create_bucket(Bucket=name)
        s3.put_object(
            Bucket="reports",
            Key="october.csv",
            Body=b"site,pallets\nS01,1200\nS02,2100\nS03,1800\n",
        )

        with record() as rec:  # <- everything the agent does on AWS is recorded
            if "--claude" in sys.argv:
                print("Claude says:", run_with_claude())
            else:
                run_scripted()

        print("\nThe agent needed only this:\n" + rec.explain())
        print("\nLeast-privilege policy for the agent:\n" + rec.policy_json())
