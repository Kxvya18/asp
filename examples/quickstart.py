"""Run: uv run python examples/quickstart.py   (no AWS account needed: moto fakes AWS)"""

import os

import boto3
from moto import mock_aws

from asp import record

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")


def archive_report(s3):
    """A tiny 'job': copy a report into an archive bucket."""
    body = s3.get_object(Bucket="reports", Key="october.csv")["Body"].read()
    s3.put_object(Bucket="archive", Key="2026/october.csv", Body=body)


with mock_aws():
    s3 = boto3.client("s3", region_name="us-east-1")
    for name in ("reports", "archive"):
        s3.create_bucket(Bucket=name)
    s3.put_object(Bucket="reports", Key="october.csv", Body=b"site,pallets\nS01,1200\n")

    with record() as rec:
        archive_report(s3)

    print(rec.policy_json())
