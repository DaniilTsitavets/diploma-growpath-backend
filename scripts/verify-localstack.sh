#!/usr/bin/env bash
# Step 1 checks against a running LocalStack: license/Cognito, gateway proxy, authorizer, throttling, SNS->SQS->DLQ.
set -uo pipefail

export AWS_ENDPOINT_URL=${AWS_ENDPOINT_URL_HOST:-http://localhost:4566}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
TEST_USER=check@growpath.test
TEST_PASSWORD='Passw0rd!Check'
STAGE=v1

say() { printf '\n== %s\n' "$*"; }
aws_() { aws --no-cli-pager "$@"; }

say "1. LocalStack license and enabled services"
curl -fs "$AWS_ENDPOINT_URL/_localstack/info" | jq '{version, edition, is_license_activated}'
curl -fs "$AWS_ENDPOINT_URL/_localstack/health" | jq '.services | with_entries(select(.key|test("apigateway|cognito-idp|sns|sqs")))'

say "2. Cognito token"
pool_id=$(aws_ cognito-idp list-user-pools --max-results 10 --query 'UserPools[?Name==`growpath`].Id|[0]' --output text)
client_id=$(aws_ cognito-idp list-user-pool-clients --user-pool-id "$pool_id" --query 'UserPoolClients[0].ClientId' --output text)
token=$(aws_ cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH --client-id "$client_id" \
  --auth-parameters "USERNAME=$TEST_USER,PASSWORD=$TEST_PASSWORD" --query AuthenticationResult.IdToken --output text)
echo "pool=$pool_id client=$client_id token_length=${#token}"

api_id=$(aws_ apigateway get-rest-apis --query 'items[?name==`growpath-gateway`].id|[0]' --output text)
base="$AWS_ENDPOINT_URL/restapis/$api_id/$STAGE/_user_request_/v1"

say "3. Gateway proxy to echo container, authorizer (expect 401 without token, 200 with token)"
echo "no token:      $(curl -s -o /dev/null -w '%{http_code}' "$base/ping")"
echo "garbage token: $(curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: garbage' "$base/ping")"
echo "valid token:   $(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: $token" "$base/ping")"
echo "upstream saw:"
curl -s -H "Authorization: $token" "$base/ping" | jq '{path, headers: (.headers | {"x-correlation-id", host})}'

say "4. Throttling (limit configured: rate 2 / burst 2; 30 rapid requests, expect some 429)"
for _ in $(seq 30); do curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: $token" "$base/ping"; done | sort | uniq -c

say "5. SNS -> SQS and DLQ"
queue_url=$(aws_ sqs get-queue-url --queue-name entitlement-order-placed --query QueueUrl --output text)
dlq_url=$(aws_ sqs get-queue-url --queue-name order-placed-dlq --query QueueUrl --output text)
topic_arn=$(aws_ sns list-topics --query 'Topics[?ends_with(TopicArn, `:order-placed`)].TopicArn|[0]' --output text)
aws_ sns publish --topic-arn "$topic_arn" --message '{"event_id":"e-1"}' \
  --message-attributes '{"correlation_id":{"DataType":"String","StringValue":"corr-1"}}' >/dev/null
echo "delivered:"; aws_ sqs receive-message --queue-url "$queue_url" --wait-time-seconds 5 --visibility-timeout 1 \
  --message-attribute-names All | jq -c '.Messages[0] | {Body, attrs: .MessageAttributes}'
echo "re-receiving without delete (maxReceiveCount=2) to push the message to the DLQ..."
for _ in 1 2 3; do sleep 2; aws_ sqs receive-message --queue-url "$queue_url" --visibility-timeout 1 >/dev/null; done
sleep 2
echo "DLQ:"; aws_ sqs receive-message --queue-url "$dlq_url" --wait-time-seconds 5 | jq -c '.Messages[0] | {Body}'
