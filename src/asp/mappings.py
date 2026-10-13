"""Translate recorded API calls into the IAM actions AWS actually checks.

Most calls map one-to-one ("s3" + "PutObject" -> "s3:PutObject"). This file lists the
exceptions. To fix a missing case, add one row and a test.
"""

from __future__ import annotations

# botocore service name -> IAM prefix, where they differ
SERVICE_PREFIX = {
    "monitoring": "cloudwatch",  # CloudWatch metrics and alarms
}

# (IAM prefix, operation) -> IAM actions needed
ACTIONS = {
    # S3
    ("s3", "ListObjects"): ["s3:ListBucket"],
    ("s3", "ListObjectsV2"): ["s3:ListBucket"],
    ("s3", "ListObjectVersions"): ["s3:ListBucketVersions"],
    ("s3", "HeadBucket"): ["s3:ListBucket"],
    ("s3", "HeadObject"): ["s3:GetObject"],
    ("s3", "ListBuckets"): ["s3:ListAllMyBuckets"],
    ("s3", "CopyObject"): ["s3:GetObject", "s3:PutObject"],
    ("s3", "DeleteObjects"): ["s3:DeleteObject"],
    ("s3", "CreateMultipartUpload"): ["s3:PutObject"],
    ("s3", "UploadPart"): ["s3:PutObject"],
    ("s3", "CompleteMultipartUpload"): ["s3:PutObject"],
    ("s3", "UploadPartCopy"): ["s3:GetObject", "s3:PutObject"],
    ("s3", "ListParts"): ["s3:ListMultipartUploadParts"],
    ("s3", "SelectObjectContent"): ["s3:GetObject"],
    # SQS / SNS batch calls use the single-item permission
    ("sqs", "SendMessageBatch"): ["sqs:SendMessage"],
    ("sqs", "DeleteMessageBatch"): ["sqs:DeleteMessage"],
    ("sqs", "ChangeMessageVisibilityBatch"): ["sqs:ChangeMessageVisibility"],
    ("sns", "PublishBatch"): ["sns:Publish"],
    # Lambda
    ("lambda", "Invoke"): ["lambda:InvokeFunction"],
    ("lambda", "InvokeAsync"): ["lambda:InvokeFunction"],
    ("lambda", "InvokeWithResponseStream"): ["lambda:InvokeFunction"],
    # Bedrock (LLMs): the Converse API is authorised as InvokeModel
    ("bedrock", "Converse"): ["bedrock:InvokeModel"],
    ("bedrock", "ConverseStream"): ["bedrock:InvokeModelWithResponseStream"],
    # needs no permission at all
    ("sts", "GetCallerIdentity"): [],
}

# DynamoDB transactions are authorised per item: Put -> PutItem, Update -> UpdateItem, ...
_DYNAMODB_TRANSACT_ITEM = {
    "Put": "PutItem",
    "Update": "UpdateItem",
    "Delete": "DeleteItem",
    "ConditionCheck": "ConditionCheckItem",
    "Get": "GetItem",
}


def iam_actions(service: str, operation: str, params: dict | None = None) -> list[str]:
    """The IAM actions needed for one API call."""
    prefix = SERVICE_PREFIX.get(service, service)
    if prefix == "dynamodb" and operation in ("TransactWriteItems", "TransactGetItems"):
        kinds = {kind for item in (params or {}).get("TransactItems", []) for kind in item}
        return sorted(
            f"dynamodb:{_DYNAMODB_TRANSACT_ITEM[k]}" for k in kinds if k in _DYNAMODB_TRANSACT_ITEM
        )
    return ACTIONS.get((prefix, operation), [f"{prefix}:{operation}"])
