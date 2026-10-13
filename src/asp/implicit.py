"""Permissions AWS checks behind the scenes, which never appear as calls of their own.

Two rules, kept simple:
  * KMS: reading or writing an S3 object encrypted with a KMS key also needs the key
    (kms:GenerateDataKey to write, kms:Decrypt to read). We see this in the request
    ("ServerSideEncryption": "aws:kms") or in AWS's response (bucket default encryption).
  * PassRole: handing an IAM role to a service (a Lambda's role, an ECS task role, ...)
    needs iam:PassRole on that role.
"""

from __future__ import annotations

import re

ROLE_ARN = re.compile(r"^arn:aws[\w-]*:iam::\d{12}:role/")

S3_WRITES = {
    "PutObject",
    "CopyObject",
    "CreateMultipartUpload",
    "UploadPart",
    "UploadPartCopy",
    "CompleteMultipartUpload",
}
S3_MULTIPART = {
    "CreateMultipartUpload",
    "UploadPart",
    "UploadPartCopy",
    "CompleteMultipartUpload",
}  # multipart uploads also need kms:Decrypt
S3_READS = {"GetObject", "SelectObjectContent"}


def implied(service: str, operation: str, params: dict, response: dict | None):
    """Extra (service, operation, params) permissions needed by one call."""
    extra = []
    for value in _all_strings(params):
        if ROLE_ARN.match(value):
            extra.append(("iam", "PassRole", {"RoleArn": value}))
    if service == "ec2" and operation == "RunInstances" and params.get("IamInstanceProfile"):
        extra.append(("iam", "PassRole", {}))  # the role inside the profile is not visible

    if service == "s3":
        response = response or {}
        sse = params.get("ServerSideEncryption") or response.get("ServerSideEncryption") or ""
        if sse.startswith("aws:kms"):
            key = {"KeyId": params.get("SSEKMSKeyId") or response.get("SSEKMSKeyId")}
            if operation in S3_WRITES:
                extra.append(("kms", "GenerateDataKey", key))
            if operation in S3_MULTIPART or operation in S3_READS:
                extra.append(("kms", "Decrypt", key))
    return extra


def _all_strings(value):
    """Every string inside nested dicts and lists."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _all_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _all_strings(v)
