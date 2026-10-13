import json

import boto3
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from moto import mock_aws

from asp import record
from asp.recorder import _original_make_api_call


@pytest.fixture(autouse=True)
def fake_aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    with mock_aws():
        yield


def make_bucket(s3, name="demo"):
    s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})


def test_records_s3_actions():
    s3 = boto3.client("s3")
    make_bucket(s3)  # before recording: must not appear
    with record() as rec:
        s3.put_object(Bucket="demo", Key="a.txt", Body=b"hi")
        s3.get_object(Bucket="demo", Key="a.txt")
        s3.get_object(Bucket="demo", Key="a.txt")
    assert rec.actions() == ["s3:GetObject", "s3:PutObject"]


def test_policy_is_valid_iam_json():
    with record() as rec:
        boto3.client("dynamodb").list_tables()
    p = json.loads(rec.policy_json())
    assert p["Version"] == "2012-10-17"
    assert p["Statement"] == [
        {"Effect": "Allow", "Action": ["dynamodb:ListTables"], "Resource": "*"}
    ]


def test_failed_calls_are_still_recorded():
    s3 = boto3.client("s3")
    with record() as rec, pytest.raises(ClientError):
        s3.get_object(Bucket="does-not-exist", Key="x")
    assert rec.actions() == ["s3:GetObject"]


def test_clients_from_any_session_are_recorded():
    with record() as rec:
        boto3.Session(region_name="us-east-1").client("sqs").list_queues()
    assert rec.actions() == ["sqs:ListQueues"]


def test_nothing_is_patched_after_recording():
    with record():
        pass
    assert BaseClient._make_api_call is _original_make_api_call


def test_patch_is_removed_even_if_code_fails():
    with pytest.raises(RuntimeError), record():
        raise RuntimeError("boom")
    assert BaseClient._make_api_call is _original_make_api_call


def test_nested_recorders_both_see_inner_calls():
    s3 = boto3.client("s3")
    make_bucket(s3)
    with record() as outer:
        s3.list_buckets()
        with record() as inner:
            s3.head_bucket(Bucket="demo")
    assert outer.actions() == ["s3:HeadBucket", "s3:ListBuckets"]
    assert inner.actions() == ["s3:HeadBucket"]


def test_decorator_saves_policy(tmp_path):
    out = tmp_path / "policy.json"

    @record(save_to=out)
    def job():
        boto3.client("s3").list_buckets()

    job()
    assert json.loads(out.read_text())["Statement"][0]["Action"] == ["s3:ListBuckets"]


def test_terraform_output_contains_the_same_policy():
    with record() as rec:
        boto3.client("s3").list_buckets()
    tf = rec.terraform(policy_name="nightly-job")
    assert tf.startswith('resource "aws_iam_policy" "asp" {')
    assert 'name   = "nightly-job"' in tf
    inside = tf.split("<<POLICY\n")[1].split("\nPOLICY")[0]
    assert json.loads(inside) == rec.policy()
