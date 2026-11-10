# Changelog

## Unreleased

- `asp serve`: a recording AWS endpoint for programs in any language (`pip install "asp-iam[serve]"`).
  Point an AWS SDK at it with `AWS_ENDPOINT_URL`; it records each signed request, answers through moto,
  and writes the same least-privilege policy as `record()`
- live web view at `/_asp/`: calls as they arrive, the permission each needs, the policy and its
  plain-English explanation; `/_asp/policy`, `/_asp/terraform`, `POST /_asp/reset`
- example: a Java backend (AWS SDK for Java v2) recorded with no code changes, run in CI
- `enforce(policy)`: block any AWS call the policy does not allow, before it is sent. Raises
  `asp.AccessDenied` (a `ClientError` with code `AccessDenied`), or only logs with
  `on_violation="log"`. Enforces actions and resources (down to S3 object keys), `Deny`,
  `NotAction` / `NotResource`, wildcards, implicit `iam:PassRole` / KMS permissions and
  presigned URLs. Accepts a policy dict, JSON text, a file, or a `Recorder`.
- example: `examples/ai_agent.py --attack` shows a prompt injection stopped by `enforce()`

## 0.1.1

Fixes from external testing (both made policies too small, so code failed with AccessDenied):
- presigned URLs and presigned upload forms are now recorded (#17)
- implicit permissions are added: KMS keys for encrypted S3 objects (also with bucket default
  encryption) and `iam:PassRole` when a role is handed to a service (#18)

## 0.1.0 (first release)

- `record()`: record every AWS call made through boto3 (`with` block or decorator)
- correct IAM action names where API and IAM names differ (`mappings.py`)
- policies scoped to the exact S3 buckets, DynamoDB tables/indexes, SQS queues, SNS topics and Lambda functions used
- `rec.terraform()`: the policy as a Terraform `aws_iam_policy`
- `rec.check(policy)`: missing / unused / too-broad permissions in an existing policy
- `rec.explain()`: the policy in plain English
- pytest plugin: `pytest --asp`, `pytest --asp-check role.json`
- example: an AI agent with AWS tools gets a least-privilege policy
