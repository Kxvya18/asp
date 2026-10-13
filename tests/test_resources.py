import boto3
import pytest
from moto import mock_aws

from asp import record


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


def statements(rec):
    """{action: resource(s)} for easy checking."""
    return {a: st["Resource"] for st in rec.policy()["Statement"] for a in st["Action"]}


def test_s3_objects_and_buckets_are_scoped():
    s3 = boto3.client("s3")
    for b in ("reports", "archive"):
        s3.create_bucket(Bucket=b)
    s3.put_object(Bucket="reports", Key="oct.csv", Body=b"x")
    with record() as rec:
        s3.get_object(Bucket="reports", Key="oct.csv")
        s3.copy_object(
            Bucket="archive", Key="2026/oct.csv", CopySource={"Bucket": "reports", "Key": "oct.csv"}
        )
        s3.list_objects_v2(Bucket="archive")
    assert statements(rec) == {
        "s3:GetObject": "arn:aws:s3:::reports/*",  # read only from reports
        "s3:PutObject": "arn:aws:s3:::archive/*",  # write only to archive
        "s3:ListBucket": "arn:aws:s3:::archive",
    }


def test_actions_without_a_resource_stay_star():
    with record() as rec:
        boto3.client("s3").list_buckets()
        boto3.client("ec2").describe_instances()
    assert statements(rec) == {"ec2:DescribeInstances": "*", "s3:ListAllMyBuckets": "*"}


def test_dynamodb_table_and_index():
    ddb = boto3.client("dynamodb")
    ddb.create_table(
        TableName="jobs",
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "id", "AttributeType": "S"},
            {"AttributeName": "site", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "by_site",
                "KeySchema": [{"AttributeName": "site", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )
    with record() as rec:
        ddb.put_item(TableName="jobs", Item={"id": {"S": "1"}, "site": {"S": "S01"}})
        ddb.query(
            TableName="jobs",
            IndexName="by_site",
            KeyConditionExpression="site = :s",
            ExpressionAttributeValues={":s": {"S": "S01"}},
        )
    s = statements(rec)
    assert s["dynamodb:PutItem"] == "arn:aws:dynamodb:us-east-1:*:table/jobs"
    assert s["dynamodb:Query"] == [
        "arn:aws:dynamodb:us-east-1:*:table/jobs",
        "arn:aws:dynamodb:us-east-1:*:table/jobs/index/by_site",
    ]


def test_sqs_queue_arn_uses_account_from_queue_url():
    sqs = boto3.client("sqs")
    url = sqs.create_queue(QueueName="orders")["QueueUrl"]
    with record() as rec:
        sqs.send_message(QueueUrl=url, MessageBody="hi")
    account = url.rstrip("/").split("/")[-2]
    assert statements(rec) == {"sqs:SendMessage": f"arn:aws:sqs:us-east-1:{account}:orders"}


def test_actions_with_same_resources_share_one_statement():
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="data")
    with record() as rec:
        s3.put_object(Bucket="data", Key="a", Body=b"x")
        s3.get_object(Bucket="data", Key="a")
    assert rec.policy()["Statement"] == [
        {
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:PutObject"],
            "Resource": "arn:aws:s3:::data/*",
        }
    ]


def test_unscoped_policy_is_still_available():
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="data")
    with record() as rec:
        s3.put_object(Bucket="data", Key="a", Body=b"x")
    assert rec.policy(scope_resources=False)["Statement"][0]["Resource"] == "*"
