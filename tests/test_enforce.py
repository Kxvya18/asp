import json
import logging

import boto3
import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from moto import mock_aws

from asp import AccessDenied, enforce, record
from asp.recorder import _original_make_api_call

READ_REPORTS_WRITE_ARCHIVE = {
    "Version": "2012-10-17",
    "Statement": [
        {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::reports"},
        {"Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::reports/*"},
        {"Effect": "Allow", "Action": "s3:PutObject", "Resource": "arn:aws:s3:::archive/*"},
    ],
}


@pytest.fixture(autouse=True)
def fake_aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        s3 = boto3.client("s3")
        for name in ("reports", "archive"):
            s3.create_bucket(Bucket=name)
        s3.put_object(Bucket="reports", Key="october.csv", Body=b"site,pallets\n")
        yield


def test_allowed_calls_go_through():
    s3 = boto3.client("s3")
    with enforce(READ_REPORTS_WRITE_ARCHIVE) as guard:
        s3.list_objects_v2(Bucket="reports")
        s3.get_object(Bucket="reports", Key="october.csv")
        s3.put_object(Bucket="archive", Key="summary.txt", Body=b"ok")
    assert guard.violations == []
    assert s3.get_object(Bucket="archive", Key="summary.txt")["Body"].read() == b"ok"


def test_disallowed_call_is_blocked_before_it_reaches_aws():
    s3 = boto3.client("s3")
    with enforce(READ_REPORTS_WRITE_ARCHIVE) as guard, pytest.raises(AccessDenied) as err:
        s3.delete_object(Bucket="archive", Key="anything")
    assert err.value.response["Error"]["Code"] == "AccessDenied"
    assert "s3:DeleteObject on arn:aws:s3:::archive/anything" in str(err.value)
    assert [(v.action, v.reason) for v in guard.violations] == [("s3:DeleteObject", "not allowed")]
    # the bucket is untouched: nothing was sent
    assert s3.list_buckets()["Buckets"]


def test_blocked_call_never_hits_the_network():
    s3 = boto3.client("s3")
    sent = []
    s3.meta.events.register("before-send", lambda **kw: sent.append(kw))
    with enforce(READ_REPORTS_WRITE_ARCHIVE):
        with pytest.raises(AccessDenied):
            s3.delete_bucket(Bucket="archive")
        assert sent == []  # the blocked call was never sent
        s3.list_objects_v2(Bucket="reports")
        assert len(sent) == 1  # an allowed call is (so the probe above really works)


def test_access_denied_is_a_client_error():
    """Code that already handles AWS errors handles asp's block the same way."""
    with enforce(READ_REPORTS_WRITE_ARCHIVE), pytest.raises(ClientError):
        boto3.client("s3").delete_bucket(Bucket="reports")


def test_resources_are_enforced_not_just_actions():
    s3 = boto3.client("s3")
    with enforce(READ_REPORTS_WRITE_ARCHIVE):
        with pytest.raises(AccessDenied):  # PutObject is allowed, but only into 'archive'
            s3.put_object(Bucket="reports", Key="x", Body=b"")
        with pytest.raises(AccessDenied):  # GetObject is allowed, but only from 'reports'
            s3.get_object(Bucket="archive", Key="x")


def test_object_keys_are_enforced_precisely():
    policy = {
        "Statement": [
            {"Effect": "Allow", "Action": "s3:GetObject", "Resource": "arn:aws:s3:::reports/2025/*"}
        ]
    }
    s3 = boto3.client("s3")
    s3.put_object(Bucket="reports", Key="2025/q1.csv", Body=b"")
    with enforce(policy):
        s3.get_object(Bucket="reports", Key="2025/q1.csv")
        with pytest.raises(AccessDenied):
            s3.get_object(Bucket="reports", Key="october.csv")


def test_every_key_of_a_batch_delete_is_checked():
    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "s3:DeleteObject",
                "Resource": "arn:aws:s3:::reports/tmp/*",
            }
        ]
    }
    with enforce(policy), pytest.raises(AccessDenied):
        boto3.client("s3").delete_objects(
            Bucket="reports",
            Delete={"Objects": [{"Key": "tmp/a"}, {"Key": "october.csv"}]},
        )


def test_deny_beats_allow():
    policy = {
        "Statement": [
            {"Effect": "Allow", "Action": "s3:*", "Resource": "*"},
            {"Effect": "Deny", "Action": "s3:Delete*", "Resource": "*"},
        ]
    }
    s3 = boto3.client("s3")
    with enforce(policy) as guard:
        s3.list_buckets()
        with pytest.raises(AccessDenied):
            s3.delete_bucket(Bucket="archive")
    assert guard.violations[0].reason == "denied"


def test_wildcards_and_case_in_action_names():
    policy = {"Statement": [{"Effect": "Allow", "Action": "S3:get*", "Resource": "*"}]}
    with enforce(policy):
        boto3.client("s3").get_object(Bucket="reports", Key="october.csv")


def test_not_action_and_not_resource():
    policy = {
        "Statement": [
            {"Effect": "Allow", "NotAction": "s3:Delete*", "Resource": "*"},
            {
                "Effect": "Deny",
                "Action": "s3:*",
                "NotResource": ["arn:aws:s3:::reports", "arn:aws:s3:::reports/*"],
            },
        ]
    }
    s3 = boto3.client("s3")
    with enforce(policy):
        s3.get_object(Bucket="reports", Key="october.csv")
        with pytest.raises(AccessDenied):
            s3.delete_object(Bucket="reports", Key="october.csv")  # NotAction excludes deletes
        with pytest.raises(AccessDenied):
            s3.get_object(Bucket="archive", Key="x")  # Deny everything outside 'reports'


def test_unknown_account_id_matches_a_policy_that_names_it():
    """asp cannot see the account id of a table, so a policy naming it must still match."""
    ddb = boto3.client("dynamodb")
    ddb.create_table(
        TableName="orders",
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    policy = {
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "dynamodb:PutItem",
                "Resource": "arn:aws:dynamodb:us-east-1:123456789012:table/orders",
            }
        ]
    }
    with enforce(policy):
        ddb.put_item(TableName="orders", Item={"id": {"S": "1"}})
        with pytest.raises(AccessDenied):
            ddb.delete_table(TableName="orders")


def test_log_mode_lets_calls_through_and_keeps_the_violations(caplog):
    s3 = boto3.client("s3")
    with (
        caplog.at_level(logging.WARNING, logger="asp"),
        enforce(READ_REPORTS_WRITE_ARCHIVE, on_violation="log") as guard,
    ):
        s3.delete_object(Bucket="reports", Key="october.csv")
    assert [v.action for v in guard.violations] == ["s3:DeleteObject"]
    assert "s3:DeleteObject" in caplog.text
    assert "would have been blocked" in guard.summary()


def test_invalid_mode_is_rejected():
    with pytest.raises(ValueError):
        enforce(READ_REPORTS_WRITE_ARCHIVE, on_violation="ignore")


def test_policy_from_a_file_or_json_text(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(READ_REPORTS_WRITE_ARCHIVE))
    for policy in (path, str(path), json.dumps(READ_REPORTS_WRITE_ARCHIVE)):
        with enforce(policy), pytest.raises(AccessDenied):
            boto3.client("s3").delete_bucket(Bucket="archive")


def test_enforce_exactly_what_a_recorder_saw():
    s3 = boto3.client("s3")
    with record() as rec:
        s3.get_object(Bucket="reports", Key="october.csv")
    with enforce(rec):
        s3.get_object(Bucket="reports", Key="october.csv")
        with pytest.raises(AccessDenied):
            s3.put_object(Bucket="reports", Key="new.csv", Body=b"")


def test_recording_and_enforcing_together():
    """A blocked call is still recorded: it is what the code tried to do."""
    s3 = boto3.client("s3")
    with record() as rec, enforce(READ_REPORTS_WRITE_ARCHIVE):
        s3.get_object(Bucket="reports", Key="october.csv")
        with pytest.raises(AccessDenied):
            s3.delete_bucket(Bucket="archive")
    assert rec.actions() == ["s3:DeleteBucket", "s3:GetObject"]


def test_enforcing_inside_a_recording_still_blocks():
    with enforce(READ_REPORTS_WRITE_ARCHIVE), record(), pytest.raises(AccessDenied):
        boto3.client("s3").delete_bucket(Bucket="archive")


def test_nested_enforcers_must_all_allow():
    outer = {"Statement": [{"Effect": "Allow", "Action": "s3:*", "Resource": "*"}]}
    s3 = boto3.client("s3")
    with enforce(outer):
        s3.delete_object(Bucket="archive", Key="x")  # allowed by the outer policy alone
        with enforce(READ_REPORTS_WRITE_ARCHIVE), pytest.raises(AccessDenied):
            s3.delete_object(Bucket="archive", Key="x")


def test_pass_role_is_enforced():
    """Handing a role to a service needs iam:PassRole, even though no PassRole call is made."""
    policy = {
        "Statement": [{"Effect": "Allow", "Action": "lambda:CreateFunction", "Resource": "*"}]
    }
    with enforce(policy) as guard, pytest.raises(AccessDenied):
        boto3.client("lambda").create_function(
            FunctionName="f",
            Runtime="python3.12",
            Role="arn:aws:iam::123456789012:role/admin",
            Handler="h.handler",
            Code={"ZipFile": b"x"},
        )
    assert guard.violations[0].action == "iam:PassRole"


def test_presigned_url_for_a_disallowed_action_is_not_made():
    s3 = boto3.client("s3")
    with enforce(READ_REPORTS_WRITE_ARCHIVE):
        s3.generate_presigned_url("get_object", Params={"Bucket": "reports", "Key": "october.csv"})
        with pytest.raises(AccessDenied):
            s3.generate_presigned_url("delete_object", Params={"Bucket": "reports", "Key": "x"})


def test_decorator_form():
    @enforce(READ_REPORTS_WRITE_ARCHIVE)
    def cleanup():
        boto3.client("s3").delete_bucket(Bucket="archive")

    with pytest.raises(AccessDenied):
        cleanup()


def test_nothing_is_patched_after_enforcing():
    with enforce(READ_REPORTS_WRITE_ARCHIVE):
        pass
    assert BaseClient._make_api_call is _original_make_api_call


def test_patch_is_removed_even_when_a_call_is_blocked():
    with pytest.raises(AccessDenied), enforce(READ_REPORTS_WRITE_ARCHIVE):
        boto3.client("s3").delete_bucket(Bucket="archive")
    assert BaseClient._make_api_call is _original_make_api_call
    boto3.client("s3").delete_object(Bucket="archive", Key="x")  # no longer enforced


def test_allows_helper():
    guard = enforce(READ_REPORTS_WRITE_ARCHIVE)
    assert guard.allows("s3:GetObject", "arn:aws:s3:::reports/october.csv")
    assert not guard.allows("s3:GetObject", "arn:aws:s3:::archive/october.csv")
    assert not guard.allows("s3:DeleteBucket")
