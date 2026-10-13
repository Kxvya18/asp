# Changelog

## Unreleased

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
