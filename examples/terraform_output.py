"""Write the recorded policy as Terraform: uv run python examples/terraform_output.py
Creates examples/out/policy.tf (CI checks it with `terraform validate`)."""

import os
from pathlib import Path

import boto3
from moto import mock_aws

from asp import record

os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

with mock_aws():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="reports")
    with record() as rec:
        s3.put_object(Bucket="reports", Key="a.csv", Body=b"x")
        s3.get_object(Bucket="reports", Key="a.csv")

out = Path(__file__).parent / "out"
out.mkdir(exist_ok=True)
(out / "policy.tf").write_text(rec.terraform(policy_name="reports-writer"))
print((out / "policy.tf").read_text())
