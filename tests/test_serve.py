"""asp serve: a program that only talks HTTP to the endpoint must get the same policy
as recording its boto3 calls in-process. boto3 here stands in for a Java or Go SDK:
the server sees nothing but signed HTTP requests."""

import json
import urllib.request

import boto3
import pytest
from moto import mock_aws

from asp import record
from asp.serve import Server, parse


def nightly_job(endpoint=None):
    """Reads a report from S3, records the run in DynamoDB, tells a queue it is done."""
    kw = {
        "region_name": "us-east-1",
        "endpoint_url": endpoint,
        "aws_access_key_id": "test",
        "aws_secret_access_key": "test",
    }
    s3, ddb, sqs = (
        boto3.client("s3", **kw),
        boto3.client("dynamodb", **kw),
        boto3.client("sqs", **kw),
    )
    report = s3.get_object(Bucket="reports", Key="2026/oct.csv")["Body"].read()
    s3.put_object(Bucket="archive", Key="2026/oct.csv", Body=report)
    s3.list_objects_v2(Bucket="reports", Prefix="2026/")
    ddb.put_item(TableName="jobs", Item={"id": {"S": "oct"}, "rows": {"N": "3"}})
    queue = sqs.get_queue_url(QueueName="done")["QueueUrl"]
    sqs.send_message(QueueUrl=queue, MessageBody="oct archived")


def setup(endpoint=None):
    kw = {
        "region_name": "us-east-1",
        "endpoint_url": endpoint,
        "aws_access_key_id": "test",
        "aws_secret_access_key": "test",
    }
    s3 = boto3.client("s3", **kw)
    for bucket in ("reports", "archive"):
        s3.create_bucket(Bucket=bucket)
    s3.put_object(Bucket="reports", Key="2026/oct.csv", Body=b"a,b\n1,2\n")
    boto3.client("dynamodb", **kw).create_table(
        TableName="jobs",
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    boto3.client("sqs", **kw).create_queue(QueueName="done")


@pytest.fixture
def server():
    with Server(port=0) as s:
        yield s


def test_serve_policy_equals_in_process_policy(server):
    with mock_aws():
        setup()
        with record() as rec:
            nightly_job()
    expected = rec.policy()

    setup(server.url)
    server.reset()  # creating the test data is not part of the job
    nightly_job(server.url)

    assert server.recorder.policy() == expected
    assert "s3:ListBucket" in server.recorder.actions()  # list_objects_v2, matched by path + query


def test_web_view_and_json_pages(server):
    setup(server.url)
    server.reset()
    nightly_job(server.url)
    calls = json.load(urllib.request.urlopen(server.url + "/_asp/calls"))
    assert [c["operation"] for c in calls["calls"]] == [
        "GetObject",
        "PutObject",
        "ListObjectsV2",
        "PutItem",
        "GetQueueUrl",
        "SendMessage",
    ]
    assert calls["calls"][0]["resource"] == "reports/2026/oct.csv"
    assert (
        json.load(urllib.request.urlopen(server.url + "/_asp/policy")) == server.recorder.policy()
    )
    assert b"asp live" in urllib.request.urlopen(server.url + "/_asp").read()  # redirect to /_asp/
    assert (
        b'resource "aws_iam_policy"'
        in urllib.request.urlopen(server.url + "/_asp/terraform").read()
    )

    urllib.request.urlopen(urllib.request.Request(server.url + "/_asp/reset", method="POST"))
    assert server.recorder.calls == []


def test_failed_call_is_still_recorded(server):
    """AWS checks the permission before it knows the bucket is missing."""
    s3 = boto3.client(
        "s3",
        region_name="us-east-1",
        endpoint_url=server.url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    with pytest.raises(s3.exceptions.NoSuchBucket):
        s3.list_objects_v2(Bucket="missing")
    assert server.log[0]["status"] == 404
    assert server.recorder.actions() == ["s3:ListBucket"]


def test_parse_rest_s3_headers_pick_the_operation():
    auth = {
        "Authorization": "AWS4-HMAC-SHA256 Credential=AK/20261110/eu-west-1/s3/aws4_request, ..."
    }
    put = parse("PUT", "/archive/a.csv", auth, b"x")
    copy = parse("PUT", "/archive/a.csv", {**auth, "x-amz-copy-source": "reports/a.csv"}, b"")
    head = parse("HEAD", "/reports/a.csv", auth, b"")
    assert (put.operation, put.region, put.params["Bucket"], put.params["Key"]) == (
        "PutObject",
        "eu-west-1",
        "archive",
        "a.csv",
    )
    assert copy.operation == "CopyObject"
    assert head.operation == "HeadObject"


def test_parse_virtual_hosted_bucket_and_unsigned_requests():
    auth = {
        "Authorization": "AWS4-HMAC-SHA256 Credential=AK/20261110/us-east-1/s3/aws4_request, ...",
        "Host": "reports.localhost:5050",
    }
    call = parse("GET", "/2026/oct.csv", auth, b"")
    assert (call.operation, call.params["Bucket"], call.params["Key"]) == (
        "GetObject",
        "reports",
        "2026/oct.csv",
    )
    assert parse("GET", "/favicon.ico", {}, b"") is None
