import json

# A tiny test suite that the plugin is run against (via pytest's own `pytester` fixture).
SUITE = """
import boto3, pytest
from moto import mock_aws

@pytest.fixture
def s3(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="reports")      # setup: must NOT be in the policy
        yield client

def test_upload(s3):
    s3.put_object(Bucket="reports", Key="a.csv", Body=b"x")

def test_download(s3):
    s3.put_object(Bucket="reports", Key="b.csv", Body=b"x")
    s3.get_object(Bucket="reports", Key="b.csv")
"""


def policy_file(pytester, *actions):
    p = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Action": list(actions), "Resource": "*"}],
    }
    return pytester.makefile(".json", role=json.dumps(p))


def test_asp_writes_the_policy_the_tests_need(pytester):
    pytester.makepyfile(SUITE)
    result = pytester.runpytest("--asp")
    result.assert_outcomes(passed=2)
    policy = json.loads((pytester.path / "asp-policy.json").read_text())
    actions = sorted(a for st in policy["Statement"] for a in st["Action"])
    assert actions == ["s3:GetObject", "s3:PutObject"]  # no s3:CreateBucket from the fixture
    result.stdout.fnmatch_lines(
        ["*asp: AWS permissions used by the tests*", "*write files in bucket 'reports'*"]
    )


def test_check_passes_when_the_policy_allows_everything(pytester):
    pytester.makepyfile(SUITE)
    role = policy_file(pytester, "s3:GetObject", "s3:PutObject", "s3:DeleteObject")
    result = pytester.runpytest("--asp-check", str(role))
    assert result.ret == 0
    result.stdout.fnmatch_lines(["*unused (can be removed): 1*", "*s3:DeleteObject*"])


def test_check_fails_the_run_when_a_permission_is_missing(pytester):
    pytester.makepyfile(SUITE)
    role = policy_file(pytester, "s3:PutObject")
    result = pytester.runpytest("--asp-check", str(role))
    assert result.ret == 1
    result.stdout.fnmatch_lines(
        ["*missing (code would fail): 1*", "*s3:GetObject*", "*FAILED: the code needs permissions*"]
    )


def test_plugin_does_nothing_without_options(pytester):
    pytester.makepyfile(SUITE)
    result = pytester.runpytest()
    result.assert_outcomes(passed=2)
    assert not (pytester.path / "asp-policy.json").exists()
    assert "asp: AWS permissions" not in result.stdout.str()
