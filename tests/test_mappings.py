import boto3
import pytest
from moto import mock_aws

from asp import record
from asp.mappings import iam_actions


@pytest.fixture(autouse=True)
def fake_aws(monkeypatch):
    for k, v in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        yield


@pytest.mark.parametrize(
    "operation, expected",
    [
        ("PutObject", ["s3:PutObject"]),  # most calls map one-to-one
        ("ListObjectsV2", ["s3:ListBucket"]),
        ("HeadObject", ["s3:GetObject"]),
        ("ListBuckets", ["s3:ListAllMyBuckets"]),
        ("CopyObject", ["s3:GetObject", "s3:PutObject"]),
    ],
)
def test_s3_names(operation, expected):
    assert iam_actions("s3", operation) == expected


def test_cloudwatch_uses_its_iam_prefix_not_monitoring():
    assert iam_actions("monitoring", "PutMetricData") == ["cloudwatch:PutMetricData"]


def test_bedrock_converse_needs_invoke_model():
    assert iam_actions("bedrock", "Converse") == ["bedrock:InvokeModel"]


def test_dynamodb_transactions_are_split_per_item_kind():
    params = {"TransactItems": [{"Put": {}}, {"Update": {}}, {"Put": {}}]}
    assert iam_actions("dynamodb", "TransactWriteItems", params) == [
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
    ]


def test_end_to_end_with_real_boto3_calls():
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="bkt")
    s3.put_object(Bucket="bkt", Key="k", Body=b"x")
    with record() as rec:
        s3.list_objects_v2(Bucket="bkt")
        s3.head_object(Bucket="bkt", Key="k")
        s3.copy_object(Bucket="bkt", Key="k2", CopySource={"Bucket": "bkt", "Key": "k"})
        boto3.client("sqs").create_queue(QueueName="q")
        boto3.client("cloudwatch").put_metric_data(
            Namespace="app", MetricData=[{"MetricName": "jobs", "Value": 1}]
        )
        boto3.client("sts").get_caller_identity()
    assert rec.actions() == [
        "cloudwatch:PutMetricData",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:PutObject",
        "sqs:CreateQueue",
    ]
