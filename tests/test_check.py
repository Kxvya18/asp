import json

import boto3
from moto import mock_aws

from asp import check, record


def policy(*statements):
    return {"Version": "2012-10-17", "Statement": list(statements)}


def allow(*actions):
    return {"Effect": "Allow", "Action": list(actions), "Resource": "*"}


def test_missing_unused_and_wildcards():
    p = policy(
        allow("s3:GetObject", "s3:PutObject", "s3:DeleteObject", "dynamodb:Scan"), allow("logs:*")
    )
    r = check(p, ["s3:GetObject", "sqs:SendMessage"])
    assert r.missing == ["sqs:SendMessage"]
    assert r.unused == ["dynamodb:Scan", "s3:DeleteObject", "s3:PutObject"]
    assert r.wildcards == ["logs:*"]
    assert not r.ok


def test_wildcards_allow_matching_actions():
    r = check(policy(allow("s3:Get*")), ["s3:GetObject"])
    assert r.ok and r.wildcards == ["s3:Get*"]


def test_full_admin_is_reported_as_too_broad():
    r = check(policy({"Effect": "Allow", "Action": "*", "Resource": "*"}), ["s3:GetObject"])
    assert r.ok and r.wildcards == ["*"]


def test_deny_beats_allow():
    p = policy(allow("s3:*"), {"Effect": "Deny", "Action": "s3:DeleteObject", "Resource": "*"})
    assert check(p, ["s3:DeleteObject"]).missing == ["s3:DeleteObject"]


def test_action_names_ignore_case():
    assert check(policy(allow("S3:getobject")), ["s3:GetObject"]).ok


def test_policy_from_json_file(tmp_path):
    f = tmp_path / "role.json"
    f.write_text(json.dumps(policy(allow("s3:GetObject"))))
    assert check(f, ["s3:GetObject"]).ok
    assert check(f.read_text(), ["s3:GetObject"]).ok


def test_recorder_check_end_to_end(monkeypatch):
    for k, v in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="data")
        with record() as rec:
            s3.put_object(Bucket="data", Key="a", Body=b"x")
    r = rec.check(policy(allow("s3:*", "iam:CreateUser")))
    assert r.ok
    assert r.unused == ["iam:CreateUser"] and r.wildcards == ["s3:*"]
    assert "unused (can be removed): 1" in r.summary()


def test_statement_can_be_a_single_object():
    assert check({"Statement": allow("s3:GetObject")}, ["s3:GetObject"]).ok


def test_action_can_be_a_single_string():
    p = policy({"Effect": "Allow", "Action": "s3:GetObject", "Resource": "*"})
    assert check(p, ["s3:GetObject"]).ok
