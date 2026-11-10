"""Creates the demo's buckets, table and queue on the asp endpoint (then the recording is reset)."""

import urllib.request

import boto3

URL = "http://localhost:5050"
kw = {"endpoint_url": URL, "region_name": "us-east-1"}
kw.update(aws_access_key_id="test", aws_secret_access_key="test")
s3 = boto3.client("s3", **kw)
for bucket in ("reports", "archive"):
    s3.create_bucket(Bucket=bucket)
csv = b"loan,balance,interest\nHL0042,500000.00,116.43\n"
s3.put_object(Bucket="reports", Key="2026-10.csv", Body=csv)
boto3.client("dynamodb", **kw).create_table(
    TableName="jobs",
    KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
    AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
    BillingMode="PAY_PER_REQUEST",
)
boto3.client("sqs", **kw).create_queue(QueueName="done")
# creating the data is not part of the app, so forget those calls
urllib.request.urlopen(urllib.request.Request(URL + "/_asp/reset", method="POST"))
print("demo data ready; recording reset")
