"""Describe a policy in plain English, with ordinary code (no LLM), so it is always correct."""

from __future__ import annotations

# action -> what it lets you do (add rows for more actions); "{where}" places the resource
PHRASES = {
    "s3:GetObject": "read files",
    "s3:PutObject": "write files",
    "s3:DeleteObject": "delete files",
    "s3:ListBucket": "list files",
    "s3:ListAllMyBuckets": "list all buckets",
    "dynamodb:GetItem": "read items",
    "dynamodb:PutItem": "write items",
    "dynamodb:UpdateItem": "update items",
    "dynamodb:DeleteItem": "delete items",
    "dynamodb:Query": "query",
    "dynamodb:Scan": "scan (read everything)",
    "sqs:SendMessage": "send messages",
    "sqs:ReceiveMessage": "receive messages",
    "sqs:DeleteMessage": "delete messages",
    "sns:Publish": "publish notifications",
    "lambda:InvokeFunction": "run function",
    "bedrock:InvokeModel": "call AI models",
    "kms:GenerateDataKey": "encrypt data with {where}",
    "kms:Decrypt": "decrypt data with {where}",
    "iam:PassRole": "pass {where} to an AWS service",
}


def describe_resource(arn: str) -> str:
    if arn == "*":
        return "ANY resource"
    if arn.startswith("arn:aws:s3:::"):
        bucket = arn.removeprefix("arn:aws:s3:::").removesuffix("/*")
        return f"bucket '{bucket}'"
    if ":table/" in arn:
        table = arn.split(":table/")[1]
        if "/index/" in table:
            name, index = table.split("/index/")
            return f"table '{name}' (index '{index}')"
        return f"table '{table}'"
    if arn.startswith("arn:aws:sqs:"):
        return f"queue '{arn.rsplit(':', 1)[1]}'"
    if arn.startswith("arn:aws:sns:"):
        return f"topic '{arn.rsplit(':', 1)[1]}'"
    if ":key/" in arn:
        return f"KMS key '{arn.split(':key/')[1]}'"
    if ":role/" in arn:
        return f"role '{arn.split(':role/')[1]}'"
    if ":function:" in arn:
        return f"function '{arn.split(':function:')[1]}'"
    return arn


def explain(policy: dict) -> str:
    """One line per allowed action, e.g. "- read files in bucket 'reports'"."""
    lines = []
    for st in policy.get("Statement", []):
        actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
        arns = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
        where = ", ".join(describe_resource(a) for a in arns)
        for action in actions:
            phrase = PHRASES.get(action, action)
            if "{where}" not in phrase:  # most phrases read "<do something> in <resource>"
                phrase += " in {where}"
            lines.append("- " + phrase.format(where=where))
    return "\n".join(lines) or "- nothing"
