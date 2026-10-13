# asp: Agent Scoped Permissions

Record the AWS calls your Python code, or your **AI agent**, actually makes, and get the
**least-privilege IAM policy** for it.

```python
from asp import record

with record() as rec:
    run_my_script()            # or: agent.run("archive last month's reports")

print(rec.policy_json())
```

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::reports/*"},
    {"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": "arn:aws:s3:::archive/*"}
  ]
}
```
It only reads from `reports` and only writes to `archive`, because that's all the code did.

## Why
AI agents and scripts are often given broad AWS permissions because nobody knows what they
really need. `asp` watches what they actually do and writes the smallest policy that allows it.
It works offline with [moto](https://github.com/getmoto/moto), so it fits in your tests.

## Install
```bash
pip install asp-iam
```

## Use
```python
from asp import record

# as a block
with record() as rec:
    ...
rec.actions()       # ['dynamodb:PutItem', 's3:GetObject']
rec.policy()        # dict
rec.save("policy.json")
rec.terraform()     # the same policy as a Terraform resource

# as a decorator
@record(save_to="policy.json")
def nightly_job():
    ...
```

## Correct permission names
Python call names aren't always permission names: `list_objects_v2` needs `s3:ListBucket`,
`copy_object` needs `s3:GetObject` + `s3:PutObject`, CloudWatch calls need `cloudwatch:*`,
Bedrock's `Converse` needs `bedrock:InvokeModel`. These exceptions live in one readable table,
[`mappings.py`](src/asp/mappings.py). Found a missing one? Add a row and a test.

## Status
Early (v0.x). Policies name the exact actions and, for the services above, the exact resources.
Other services keep `"Resource": "*"`. `rec.policy(scope_resources=False)` gives the old all-`*` form.

Roadmap:
- [x] Terraform output (`rec.terraform()`)
- [x] exact IAM names where API and IAM names differ (`src/asp/mappings.py`, contributions welcome)
- [x] restrict `Resource` to the exact S3 buckets, DynamoDB tables (and indexes), SQS queues, SNS topics, Lambda functions used
- [ ] `check` mode: compare an existing policy with what the code really needs
- [ ] AI-agent example and optional plain-English explanation of a policy
- [ ] pytest plugin: `pytest --asp` produces the policy your test suite needs

## License
MIT
