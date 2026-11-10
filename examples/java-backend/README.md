# Java backend example

`ReportService` is a small Java service that uses the AWS SDK for Java v2 and knows nothing about asp:

- `GET /reports/{month}` reads a report from S3 (`reports` bucket)
- `POST /archive/{month}` copies it to `archive`, logs the run in DynamoDB (`jobs`), notifies SQS (`done`)

`./run.sh` (needs Java 17+, Maven and `pip install "asp-iam[serve]"`) starts `asp serve`, creates the
demo data, runs the service with `AWS_ENDPOINT_URL=http://localhost:5050`, calls both endpoints and prints
the policy. While it runs, http://localhost:5050/_asp/ shows the calls live.
