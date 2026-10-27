# asp: Agent Scoped Permissions

[![ci](https://github.com/Kxvya18/asp/actions/workflows/ci.yml/badge.svg)](https://github.com/Kxvya18/asp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/asp-iam)](https://pypi.org/project/asp-iam/)
[![Python](https://img.shields.io/pypi/pyversions/asp-iam)](https://pypi.org/project/asp-iam/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

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

## AI agents: give them only what they use
[`examples/ai_agent.py`](examples/ai_agent.py) gives an agent three AWS tools (list, read, write
files) and the task *"summarise the October report and save it to the archive"*. Recorded with asp:

```
The agent needed only this:
- read files in bucket 'reports'
- list files in bucket 'reports'
- write files in bucket 'archive'
```
Instead of broad S3 access "to be safe", the agent gets exactly this. If it is ever misled (for
example by instructions hidden in a file it reads), it cannot delete data or touch other buckets.

```bash
python examples/ai_agent.py            # scripted stand-in, no API key, fake AWS
python examples/ai_agent.py --claude   # real Claude drives the agent (pip install anthropic)
python examples/ai_agent.py --attack   # + a prompt injection, stopped by enforce (below)
```
`rec.explain()` produces the plain-English summary with ordinary code, not an LLM, so it is
always exactly what the policy says.

## Enforce: block anything outside the policy
Recording tells you what code needs. `enforce()` makes sure it never does more: every AWS call
is checked against a policy *before it is sent*, and a call the policy doesn't allow is stopped
inside your process.

```python
from asp import enforce

with enforce("agent-policy.json"):     # a dict, JSON text, a file, or a Recorder
    agent.run(task)
```
A blocked call raises `asp.AccessDenied`, a botocore `ClientError` with the code `AccessDenied`,
the same error AWS would return, so an agent loop that already reports tool errors to the model
keeps working. `--attack` above hides *"delete every file in the archive"* in the report the agent
reads; under `enforce(rec)` with the policy from the clean run, the misled calls never happen:

```
2 call(s) blocked:
  - s3.ListObjectsV2: asp enforce: s3:ListBucket on arn:aws:s3:::archive is not allowed ...
  - s3.DeleteObject: asp enforce: s3:DeleteObject on arn:aws:s3:::archive/september-summary.txt ...
Files still in the archive: ['october-summary.txt', 'september-summary.txt']
```

- **Actions and resources** are both enforced, down to the S3 object key (a policy for
  `reports/2025/*` blocks `reports/october.csv`). `Deny` beats `Allow`; `NotAction`,
  `NotResource` and wildcards work. Hidden permissions (`iam:PassRole`, KMS keys named in the
  request) and presigned URLs are checked too.
- **Try before you block**: `enforce(policy, on_violation="log")` lets calls through and only
  logs them (logger `"asp"`), so you can run a new policy on real traffic first.
- **See what happened**: `guard.violations` lists each blocked call, `guard.summary()` says it in
  plain English, and `guard.allows("s3:GetObject", arn)` answers a single question.

It is a guard rail inside your process, not a replacement for the role's IAM policy (AWS still
checks that). `Condition` blocks are not evaluated, and for services where asp can't see which
resource a call touches, the action alone decides.

## From your test suite (pytest plugin)
```bash
pytest --asp                    # writes asp-policy.json: the permissions your tests needed
pytest --asp-check role.json    # fails the run if the code needs actions role.json doesn't allow
```
Put `--asp-check` in CI and a change that suddenly needs a new AWS permission is caught before
it fails in production. Only calls made inside test bodies are recorded: put setup (like creating
fake buckets with moto) in fixtures so it stays out of the policy.

## Permissions AWS checks behind the scenes
Some permissions never appear as calls of their own. asp adds them:
- **KMS**: uploading or downloading an S3 object encrypted with a KMS key also needs the key
  (`kms:GenerateDataKey` / `kms:Decrypt`), including buckets with default encryption
- **iam:PassRole**: handing a role to a service (a Lambda's role, an ECS task role, ...)
- **presigned URLs**: a download link needs `s3:GetObject`, an upload form `s3:PutObject`,
  because whoever uses the link acts with your permissions

## Check an existing policy
```python
result = rec.check("role_policy.json")   # dict, JSON text or a file path
print(result.summary())
```
```
missing (code would fail): 0
unused (can be removed): 2
  - dynamodb:Scan
  - iam:CreateUser
too broad (wildcards): 1
  - s3:*
```
`result.ok` is False when the code needs something the policy doesn't allow, which is handy in CI.
This first version compares actions, not resources.

## Status
Early (v0.x). Policies name the exact actions and, for the services above, the exact resources.
Other services keep `"Resource": "*"`. `rec.policy(scope_resources=False)` gives the old all-`*` form.

Roadmap:
- [x] Terraform output (`rec.terraform()`)
- [x] exact IAM names where API and IAM names differ (`src/asp/mappings.py`, contributions welcome)
- [x] restrict `Resource` to the exact S3 buckets, DynamoDB tables (and indexes), SQS queues, SNS topics, Lambda functions used
- [x] `check` mode: compare an existing policy with what the code really needs (actions)
- [x] AI-agent example and plain-English `explain()`
- [x] pytest plugin: `pytest --asp` / `pytest --asp-check role.json`
- [x] enforce mode: block calls outside a policy at runtime (`asp.enforce()`)

## Contributing
Found an AWS call that gets the wrong permission name? Add one row to
[`mappings.py`](src/asp/mappings.py) plus a test, and open a PR. Issues labelled
**good first issue** are a good place to start.

## License
MIT
