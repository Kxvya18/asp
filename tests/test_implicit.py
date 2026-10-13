"""Issues #17 (presigned URLs) and #18 (implicit permissions)."""

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from asp import record


@pytest.fixture(autouse=True)
def fake_aws(monkeypatch):
    for k, v in {
        "AWS_ACCESS_KEY_ID": "x",
        "AWS_SECRET_ACCESS_KEY": "x",
        "AWS_DEFAULT_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        yield


@pytest.fixture
def s3():
    client = boto3.client("s3")
    client.create_bucket(Bucket="data")
    return client


def statements(rec):
    return {a: st["Resource"] for st in rec.policy()["Statement"] for a in st["Action"]}


# ---- #17 presigned URLs


def test_presigned_download_link_needs_get_object(s3):
    with record() as rec:
        s3.generate_presigned_url("get_object", Params={"Bucket": "data", "Key": "a.txt"})
    assert statements(rec) == {"s3:GetObject": "arn:aws:s3:::data/*"}


def test_presigned_upload_form_needs_put_object(s3):
    with record() as rec:
        s3.generate_presigned_post("data", "uploads/${filename}")
    assert statements(rec) == {"s3:PutObject": "arn:aws:s3:::data/*"}


def test_presign_patch_is_removed_after_recording(s3):
    from botocore.client import BaseClient

    from asp.recorder import _original_emit_api_params

    with record():
        pass
    assert BaseClient._emit_api_params is _original_emit_api_params


# ---- #18 implicit permissions: KMS


def test_upload_with_kms_key_needs_generate_data_key(s3):
    key = boto3.client("kms").create_key()["KeyMetadata"]["KeyId"]
    with record() as rec:
        s3.put_object(
            Bucket="data", Key="a", Body=b"x", ServerSideEncryption="aws:kms", SSEKMSKeyId=key
        )
    assert statements(rec)["kms:GenerateDataKey"] == f"arn:aws:kms:us-east-1:*:key/{key}"


def test_download_of_kms_object_needs_decrypt(s3):
    key = boto3.client("kms").create_key()["KeyMetadata"]["KeyId"]
    s3.put_object(
        Bucket="data", Key="a", Body=b"x", ServerSideEncryption="aws:kms", SSEKMSKeyId=key
    )
    with record() as rec:  # nothing in the request says KMS: only AWS's response does
        s3.get_object(Bucket="data", Key="a")
    assert "kms:Decrypt" in statements(rec)


def test_plain_upload_needs_no_kms(s3):
    with record() as rec:
        s3.put_object(Bucket="data", Key="a", Body=b"x")
    assert rec.actions() == ["s3:PutObject"]


# ---- #18 implicit permissions: iam:PassRole


def test_creating_a_lambda_with_a_role_needs_pass_role():
    role = "arn:aws:iam::123456789012:role/lambda-exec"
    with record() as rec, pytest.raises(ClientError):  # moto rejects the made-up role
        boto3.client("lambda").create_function(
            FunctionName="f", Runtime="python3.12", Role=role, Handler="h.h", Code={"ZipFile": b"x"}
        )
    assert statements(rec)["iam:PassRole"] == role
    assert "lambda:CreateFunction" in rec.actions()


def test_explain_reads_naturally_for_kms_and_pass_role():
    from asp.explain import explain

    p = {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["kms:Decrypt"],
                "Resource": "arn:aws:kms:us-east-1:*:key/abc",
            },
            {
                "Effect": "Allow",
                "Action": ["iam:PassRole"],
                "Resource": "arn:aws:iam::123456789012:role/lambda-exec",
            },
        ]
    }
    assert explain(p).splitlines() == [
        "- decrypt data with KMS key 'abc'",
        "- pass role 'lambda-exec' to an AWS service",
    ]
