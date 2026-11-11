#!/usr/bin/env bash
# Checks a running stack: LocalStack and its provisioning, the gateway (authorizer, routes, identity
# headers, throttling), SNS->SQS->DLQ, and the health of every service.
set -uo pipefail

export AWS_ENDPOINT_URL=${AWS_ENDPOINT_URL_HOST:-http://localhost:4566}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
TEST_USER=check@growpath.test
TEST_PASSWORD='Passw0rd!Check'
EXPERT_USER=expert@growpath.test
EXPERT_PASSWORD='Passw0rd!Expert'
STAGE=v1
SERVICE_PORTS="commerce:8001 entitlement:8002 plant-care:8003 expert-consultation:8004"
FAILED=0

say() { printf '\n== %s\n' "$*"; }
aws_() { aws --no-cli-pager "$@"; }

# expect <what> <expected> <actual>
expect() {
  if [ "$2" = "$3" ]; then printf '  ok    %s: %s\n' "$1" "$3"
  else printf '  FAIL  %s: expected %s, got %s\n' "$1" "$2" "$3"; FAILED=1; fi
}

status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

login() {
  aws_ cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH --client-id "$client_id" \
    --auth-parameters "USERNAME=$1,PASSWORD=$2" --query AuthenticationResult.IdToken --output text
}

say "1. LocalStack license and enabled services"
curl -fs "$AWS_ENDPOINT_URL/_localstack/info" | jq '{version, edition, is_license_activated}'
curl -fs "$AWS_ENDPOINT_URL/_localstack/health" | jq '.services | with_entries(select(.key|test("apigateway|cognito-idp|sns|sqs|s3")))'

say "2. Cognito tokens"
pool_id=$(aws_ cognito-idp list-user-pools --max-results 10 --query 'UserPools[?Name==`growpath`].Id|[0]' --output text)
client_id=$(aws_ cognito-idp list-user-pool-clients --user-pool-id "$pool_id" --query 'UserPoolClients[0].ClientId' --output text)
token=$(login "$TEST_USER" "$TEST_PASSWORD")
expert_token=$(login "$EXPERT_USER" "$EXPERT_PASSWORD")
echo "pool=$pool_id client=$client_id user_token_length=${#token} expert_token_length=${#expert_token}"

api_id=$(aws_ apigateway get-rest-apis --query 'items[?name==`growpath-gateway`].id|[0]' --output text)
base="$AWS_ENDPOINT_URL/restapis/$api_id/$STAGE/_user_request_/v1"

say "3. Authorizer: no token and a garbage token are refused"
expect "no token" 401 "$(status "$base/items")"
expect "garbage token" 401 "$(status -H 'Authorization: garbage' "$base/items")"
expect "valid token" 200 "$(status -H "Authorization: $token" "$base/items")"

say "4. Routes reach the right service"
expect "GET /items (commerce)" 200 "$(status -H "Authorization: $token" "$base/items")"
expect "GET /journals (plant-care)" 200 "$(status -H "Authorization: $token" "$base/journals")"
expect "GET /me/entitlement (entitlement)" 200 "$(status -H "Authorization: $token" "$base/me/entitlement")"
expect "GET /expert/tickets (expert-consultation, expert)" 200 "$(status -H "Authorization: $expert_token" "$base/expert/tickets")"
expect "internal quota API is not routed" 403 "$(status -H "Authorization: $token" "$base/quotas/ai/reservations")"

say "5. Identity headers come from the token, not from the client"
expect "user without the experts group" 403 "$(status -H "Authorization: $token" "$base/expert/tickets")"
expect "user forges X-User-Groups: experts" 403 \
  "$(status -H "Authorization: $token" -H 'X-User-Groups: experts' "$base/expert/tickets")"
expect "user forges X-User-Id (replaced by the sub claim)" 200 \
  "$(status -H "Authorization: $token" -H 'X-User-Id: not-a-uuid' "$base/journals")"
sub=$(python3 -c 'import base64, json, sys
payload = sys.argv[1].split(".")[1]
print(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"])' "$token")
expect "entitlement answers for the sub of the token" "$sub" \
  "$(curl -s -H "Authorization: $token" "$base/me/entitlement" | jq -r .user_id)"

say "6. Throttling (informational: LocalStack stores the limit but does not enforce it, so only 200 is expected)"
for _ in $(seq 30); do curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: $token" "$base/items"; done | sort | uniq -c

say "7. SNS -> SQS and DLQ"
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

say "8. Provisioned queues, topics and bucket"
queues=$(aws_ sqs list-queues --query 'QueueUrls[]' --output text | tr '\t' '\n' | sed 's#.*/##' | sort | tr '\n' ' ')
for name in entitlement-order-placed plant-care-order-placed entitlement-ticket-events plant-care-ticket-events \
  expert-escalation-requested order-placed-dlq plant-care-order-placed-dlq entitlement-ticket-events-dlq \
  plant-care-ticket-events-dlq expert-escalation-requested-dlq; do
  case " $queues" in *" $name "*) expect "queue $name" yes yes ;; *) expect "queue $name" yes no ;; esac
done
topics=$(aws_ sns list-topics --query 'Topics[].TopicArn' --output text)
for name in order-placed ticket-events escalation-requested; do
  case "$topics" in *":$name"*) expect "topic $name" yes yes ;; *) expect "topic $name" yes no ;; esac
done
aws_ s3api head-bucket --bucket growpath-photos >/dev/null 2>&1 && expect "bucket growpath-photos" yes yes \
  || expect "bucket growpath-photos" yes no

say "9. Service readiness (database and queues)"
for entry in $SERVICE_PORTS; do
  expect "${entry%%:*} /health/ready" 200 "$(status "http://127.0.0.1:${entry##*:}/health/ready")"
done

echo
if [ "$FAILED" -eq 0 ]; then echo "verify: all checks passed"; else echo "verify: some checks FAILED"; fi
exit "$FAILED"
