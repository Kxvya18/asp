"""Work out which AWS resources (ARNs) a call touched, so a policy can name them instead of
using "*". Services not listed here, and calls without a specific resource, keep "*".
The account id is written as "*" when the call does not reveal it.
"""

from __future__ import annotations

# S3 actions that act on objects (bucket/key); all other S3 actions act on the bucket itself
S3_OBJECT_ACTIONS = {
    "GetObject",
    "PutObject",
    "DeleteObject",
    "GetObjectAcl",
    "PutObjectAcl",
    "GetObjectTagging",
    "PutObjectTagging",
    "DeleteObjectTagging",
    "RestoreObject",
    "AbortMultipartUpload",
    "ListMultipartUploadParts",
    "GetObjectVersion",
    "DeleteObjectVersion",
    "GetObjectAttributes",
}


def resources(action: str, params: dict, region: str | None) -> list[str]:
    """ARNs that `action` needs for one call with these parameters."""
    service, name = action.split(":", 1)
    region = region or "*"
    if service == "s3":
        return _s3(name, params)
    if service == "dynamodb":
        return _dynamodb(params, region)
    if service == "sqs":
        return _sqs(params, region)
    if service == "sns":
        return _sns(params, region)
    if service == "lambda":
        return _lambda(params, region)
    return ["*"]


def _s3(name, params):
    bucket = params.get("Bucket")
    if name == "GetObject" and "CopySource" in params:  # reading the source of a copy
        bucket = _copy_source_bucket(params["CopySource"])
    if not bucket:
        return ["*"]
    if name in S3_OBJECT_ACTIONS:
        return [f"arn:aws:s3:::{bucket}/*"]
    return [f"arn:aws:s3:::{bucket}"]


def _copy_source_bucket(source):
    if isinstance(source, dict):
        return source.get("Bucket")
    return str(source).lstrip("/").split("/", 1)[0]  # "bucket/key"


def _dynamodb(params, region):
    tables = set()
    if "TableName" in params:
        tables.add(params["TableName"])
    tables.update(params.get("RequestItems", {}))  # batch calls: {table: [...]}
    for item in params.get("TransactItems", []):  # transactions: [{"Put": {"TableName": ..}}]
        for body in item.values():
            if "TableName" in body:
                tables.add(body["TableName"])
    if not tables:
        return ["*"]
    arns = [f"arn:aws:dynamodb:{region}:*:table/{t}" for t in sorted(tables)]
    if params.get("IndexName") and "TableName" in params:
        arns.append(
            f"arn:aws:dynamodb:{region}:*:table/{params['TableName']}/index/{params['IndexName']}"
        )
    return arns


def _sqs(params, region):
    if "QueueUrl" in params:  # https://sqs.<region>.amazonaws.com/<account>/<name>
        parts = params["QueueUrl"].rstrip("/").split("/")
        return [f"arn:aws:sqs:{region}:{parts[-2]}:{parts[-1]}"]
    if "QueueName" in params:
        return [f"arn:aws:sqs:{region}:*:{params['QueueName']}"]
    return ["*"]


def _sns(params, region):
    if "TopicArn" in params:
        return [params["TopicArn"]]
    if "Name" in params:  # CreateTopic
        return [f"arn:aws:sns:{region}:*:{params['Name']}"]
    return ["*"]


def _lambda(params, region):
    name = params.get("FunctionName")
    if not name:
        return ["*"]
    if name.startswith("arn:"):
        return [name]
    return [f"arn:aws:lambda:{region}:*:function:{name}"]
