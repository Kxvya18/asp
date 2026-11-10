#!/bin/bash
# Record what a Java backend needs from AWS, with no change to its code:
#   1. start `asp serve` (a fake AWS that records)   2. create the demo data, then reset
#   3. run the Java service against it, call its endpoints   4. print the least-privilege policy
# Needs: Java 17+, Maven, and asp with the serve extra (pip install "asp-iam[serve]").
set -e
cd "$(dirname "$0")"
asp serve --port 5050 & ASP=$!
trap 'kill $ASP $JAVA 2>/dev/null' EXIT
until curl -s localhost:5050/_asp/policy >/dev/null; do sleep 0.5; done
python setup_data.py

export AWS_ENDPOINT_URL=http://localhost:5050 AWS_REGION=us-east-1 AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
mvn -q compile exec:java & JAVA=$!
until curl -s localhost:8080/health >/dev/null; do sleep 1; done

curl -s localhost:8080/reports/2026-10
curl -s -X POST localhost:8080/archive/2026-10
echo; echo "Least-privilege policy for the Java backend (also live at http://localhost:5050/_asp/):"
curl -s localhost:5050/_asp/policy
