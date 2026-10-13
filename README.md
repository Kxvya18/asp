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
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": "*"}
  ]
}
```

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

## Status
Early (v0.1). Policies list the right **actions**, with `"Resource": "*"`.

Roadmap:
- [x] Terraform output (`rec.terraform()`)
- [ ] exact IAM names for services whose API names differ from their IAM names
- [ ] restrict `Resource` to the exact buckets / tables / queues used
- [ ] `check` mode: compare an existing policy with what the code really needs
- [ ] AI-agent example and optional plain-English explanation of a policy
- [ ] pytest plugin: `pytest --asp` produces the policy your test suite needs

## License
MIT
