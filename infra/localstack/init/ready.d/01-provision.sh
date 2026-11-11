#!/bin/bash
# Runs inside LocalStack once it is ready: Cognito, API Gateway (authorizer, routes, throttling), SNS/SQS/DLQ.
set -euo pipefail

POOL_NAME=growpath
CLIENT_NAME=growpath-client
TEST_USER=check@growpath.test
TEST_PASSWORD='Passw0rd!Check'
EXPERT_USER=expert@growpath.test
EXPERT_PASSWORD='Passw0rd!Expert'
USER_GROUP=users
EXPERT_GROUP=experts
STAGE=v1
COMMERCE=http://commerce:8000
ENTITLEMENT=http://entitlement:8000
PLANT_CARE=http://plant-care:8000
EXPERT_CONSULTATION=http://expert-consultation:8000
RATE_LIMIT=2
BURST_LIMIT=2

pool_id=$(awslocal cognito-idp create-user-pool --pool-name "$POOL_NAME" --query UserPool.Id --output text)
pool_arn="arn:aws:cognito-idp:${AWS_DEFAULT_REGION:-us-east-1}:000000000000:userpool/${pool_id}"
client_id=$(awslocal cognito-idp create-user-pool-client --user-pool-id "$pool_id" --client-name "$CLIENT_NAME" \
  --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH --query UserPoolClient.ClientId --output text)
awslocal cognito-idp admin-create-user --user-pool-id "$pool_id" --username "$TEST_USER" \
  --message-action SUPPRESS >/dev/null
awslocal cognito-idp admin-set-user-password --user-pool-id "$pool_id" --username "$TEST_USER" \
  --password "$TEST_PASSWORD" --permanent
awslocal cognito-idp create-group --user-pool-id "$pool_id" --group-name "$USER_GROUP" >/dev/null
awslocal cognito-idp create-group --user-pool-id "$pool_id" --group-name "$EXPERT_GROUP" >/dev/null
awslocal cognito-idp admin-create-user --user-pool-id "$pool_id" --username "$EXPERT_USER" \
  --message-action SUPPRESS >/dev/null
awslocal cognito-idp admin-set-user-password --user-pool-id "$pool_id" --username "$EXPERT_USER" \
  --password "$EXPERT_PASSWORD" --permanent
for member in "$TEST_USER" "$EXPERT_USER"; do
  awslocal cognito-idp admin-add-user-to-group --user-pool-id "$pool_id" --username "$member" \
    --group-name "$USER_GROUP"
done
awslocal cognito-idp admin-add-user-to-group --user-pool-id "$pool_id" --username "$EXPERT_USER" \
  --group-name "$EXPERT_GROUP"

api_id=$(awslocal apigateway create-rest-api --name growpath-gateway --query id --output text)
root_id=$(awslocal apigateway get-resources --rest-api-id "$api_id" --query 'items[0].id' --output text)
v1_id=$(awslocal apigateway create-resource --rest-api-id "$api_id" --parent-id "$root_id" --path-part v1 --query id --output text)
auth_id=$(awslocal apigateway create-authorizer --rest-api-id "$api_id" --name cognito --type COGNITO_USER_POOLS \
  --provider-arns "$pool_arn" --identity-source method.request.header.Authorization --query id --output text)

# Every route sets the identity headers from the verified JWT claims. A mapped header replaces the one
# the client sent, but only when the claim has a value: a token without `cognito:groups` would let a
# forged X-User-Groups through. That is why every user belongs to the `users` group.
IDENTITY_HEADERS="\"integration.request.header.X-Correlation-Id\":\"context.requestId\",\"integration.request.header.X-User-Id\":\"context.authorizer.claims.sub\",\"integration.request.header.X-User-Groups\":\"context.authorizer.claims['cognito:groups']\""

# Prints the id of /v1/<path>, creating the missing parts.
resource_for() {
  local parent=$v1_id part id
  for part in ${1//\// }; do
    id=$(awslocal apigateway get-resources --rest-api-id "$api_id" \
      --query "items[?parentId=='${parent}' && pathPart=='${part}'].id|[0]" --output text)
    if [ "$id" = None ]; then
      id=$(awslocal apigateway create-resource --rest-api-id "$api_id" --parent-id "$parent" \
        --path-part "$part" --query id --output text)
    fi
    parent=$id
  done
  echo "$parent"
}

# route <path under /v1> <http method> <upstream uri> [extra integration parameters] [method parameters]
route() {
  local resource_id method=$2 uri=$3 extra=${4:+$4,} declared=${5:-}
  resource_id=$(resource_for "$1")
  awslocal apigateway put-method --rest-api-id "$api_id" --resource-id "$resource_id" --http-method "$method" \
    --authorization-type COGNITO_USER_POOLS --authorizer-id "$auth_id" \
    ${declared:+--request-parameters "$declared"} >/dev/null
  awslocal apigateway put-integration --rest-api-id "$api_id" --resource-id "$resource_id" --http-method "$method" \
    --type HTTP_PROXY --integration-http-method "$method" --uri "$uri" \
    --request-parameters "{${extra}${IDENTITY_HEADERS}}" >/dev/null
}

# A prefix: the path itself and everything below it go to the same upstream path.
prefix_route() {
  route "$1" ANY "$2/v1/$1"
  route "$1/{proxy+}" ANY "$2/v1/$1/{proxy}" '"integration.request.path.proxy":"method.request.path.proxy"' \
    method.request.path.proxy=true
}

route items GET "$COMMERCE/v1/items"
route orders POST "$COMMERCE/v1/orders"
prefix_route journals "$PLANT_CARE"
route 'updates/{proxy+}' ANY "$PLANT_CARE/v1/updates/{proxy}" '"integration.request.path.proxy":"method.request.path.proxy"' \
  method.request.path.proxy=true
prefix_route expert/tickets "$EXPERT_CONSULTATION"
# The user id comes from the token, never from the URL: entitlement serves any user id it is given.
route me/entitlement GET "$ENTITLEMENT/v1/entitlements/{userId}" '"integration.request.path.userId":"context.authorizer.claims.sub"'

awslocal apigateway create-deployment --rest-api-id "$api_id" --stage-name "$STAGE" >/dev/null
awslocal apigateway update-stage --rest-api-id "$api_id" --stage-name "$STAGE" --patch-operations \
  "op=replace,path=/*/*/throttling/rateLimit,value=${RATE_LIMIT}" \
  "op=replace,path=/*/*/throttling/burstLimit,value=${BURST_LIMIT}" >/dev/null

# A subscriber queue with its dead-letter queue; prints the queue ARN. Short visibility timeout and
# two receives keep the DLQ demo quick.
create_queue_with_dlq() {
  local queue_name=$1 dlq_name=$2 dlq_url dlq_arn queue_url
  dlq_url=$(awslocal sqs create-queue --queue-name "$dlq_name" --query QueueUrl --output text)
  dlq_arn=$(awslocal sqs get-queue-attributes --queue-url "$dlq_url" --attribute-names QueueArn --query Attributes.QueueArn --output text)
  queue_url=$(awslocal sqs create-queue --queue-name "$queue_name" --attributes \
    "{\"VisibilityTimeout\":\"1\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${dlq_arn}\\\",\\\"maxReceiveCount\\\":\\\"2\\\"}\"}" \
    --query QueueUrl --output text)
  awslocal sqs get-queue-attributes --queue-url "$queue_url" --attribute-names QueueArn --query Attributes.QueueArn --output text
}

# Subscribe a queue to a topic with raw delivery; the optional third argument is a filter policy
# on the event_type message attribute.
subscribe_queue() {
  local topic_arn=$1 queue_arn=$2 filter_policy=${3:-} subscription_arn
  subscription_arn=$(awslocal sns subscribe --topic-arn "$topic_arn" --protocol sqs \
    --notification-endpoint "$queue_arn" --attributes RawMessageDelivery=true \
    --query SubscriptionArn --output text)
  if [ -n "$filter_policy" ]; then
    awslocal sns set-subscription-attributes --subscription-arn "$subscription_arn" \
      --attribute-name FilterPolicy --attribute-value "$filter_policy"
  fi
}

# commerce publishes order.placed; entitlement and plant-care consume it.
order_topic_arn=$(awslocal sns create-topic --name order-placed --query TopicArn --output text)
subscribe_queue "$order_topic_arn" "$(create_queue_with_dlq entitlement-order-placed order-placed-dlq)"
subscribe_queue "$order_topic_arn" \
  "$(create_queue_with_dlq plant-care-order-placed plant-care-order-placed-dlq)"

# expert-consultation publishes ticket.created|answered|cancelled; entitlement needs only the
# two that change its quota.
ticket_topic_arn=$(awslocal sns create-topic --name ticket-events --query TopicArn --output text)
subscribe_queue "$ticket_topic_arn" \
  "$(create_queue_with_dlq entitlement-ticket-events entitlement-ticket-events-dlq)" \
  '{"event_type":["ticket.created","ticket.cancelled"]}'
# plant-care shows the whole ticket lifecycle, so its subscription has no filter.
subscribe_queue "$ticket_topic_arn" \
  "$(create_queue_with_dlq plant-care-ticket-events plant-care-ticket-events-dlq)"

# plant-care publishes escalation.requested; expert-consultation subscribes with its own queue.
escalation_topic_arn=$(awslocal sns create-topic --name escalation-requested --query TopicArn --output text)
subscribe_queue "$escalation_topic_arn" \
  "$(create_queue_with_dlq expert-escalation-requested expert-escalation-requested-dlq)"

# plant-care issues presigned URLs for update photos; only object keys are stored in its database.
awslocal s3api create-bucket --bucket growpath-photos >/dev/null

echo "provisioned: api=${api_id} stage=${STAGE} pool=${pool_id} client=${client_id}"
