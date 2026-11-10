#!/bin/bash
# Runs inside LocalStack once it is ready: Cognito, API Gateway (authorizer, proxy, throttling), SNS/SQS/DLQ.
set -euo pipefail

POOL_NAME=growpath
CLIENT_NAME=growpath-client
TEST_USER=check@growpath.test
TEST_PASSWORD='Passw0rd!Check'
STAGE=v1
UPSTREAM=http://echo:8080
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

api_id=$(awslocal apigateway create-rest-api --name growpath-gateway --query id --output text)
root_id=$(awslocal apigateway get-resources --rest-api-id "$api_id" --query 'items[0].id' --output text)
v1_id=$(awslocal apigateway create-resource --rest-api-id "$api_id" --parent-id "$root_id" --path-part v1 --query id --output text)
proxy_id=$(awslocal apigateway create-resource --rest-api-id "$api_id" --parent-id "$v1_id" --path-part '{proxy+}' --query id --output text)
auth_id=$(awslocal apigateway create-authorizer --rest-api-id "$api_id" --name cognito --type COGNITO_USER_POOLS \
  --provider-arns "$pool_arn" --identity-source method.request.header.Authorization --query id --output text)

awslocal apigateway put-method --rest-api-id "$api_id" --resource-id "$proxy_id" --http-method ANY \
  --authorization-type COGNITO_USER_POOLS --authorizer-id "$auth_id" \
  --request-parameters method.request.path.proxy=true >/dev/null
awslocal apigateway put-integration --rest-api-id "$api_id" --resource-id "$proxy_id" --http-method ANY \
  --type HTTP_PROXY --integration-http-method ANY --uri "${UPSTREAM}/v1/{proxy}" \
  --request-parameters '{"integration.request.path.proxy":"method.request.path.proxy","integration.request.header.X-Correlation-Id":"context.requestId"}' >/dev/null

awslocal apigateway create-deployment --rest-api-id "$api_id" --stage-name "$STAGE" >/dev/null
awslocal apigateway update-stage --rest-api-id "$api_id" --stage-name "$STAGE" --patch-operations \
  "op=replace,path=/*/*/throttling/rateLimit,value=${RATE_LIMIT}" \
  "op=replace,path=/*/*/throttling/burstLimit,value=${BURST_LIMIT}" >/dev/null

dlq_url=$(awslocal sqs create-queue --queue-name order-placed-dlq --query QueueUrl --output text)
dlq_arn=$(awslocal sqs get-queue-attributes --queue-url "$dlq_url" --attribute-names QueueArn --query Attributes.QueueArn --output text)
queue_url=$(awslocal sqs create-queue --queue-name entitlement-order-placed --attributes \
  "{\"VisibilityTimeout\":\"1\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"${dlq_arn}\\\",\\\"maxReceiveCount\\\":\\\"2\\\"}\"}" \
  --query QueueUrl --output text)
queue_arn=$(awslocal sqs get-queue-attributes --queue-url "$queue_url" --attribute-names QueueArn --query Attributes.QueueArn --output text)
topic_arn=$(awslocal sns create-topic --name order-placed --query TopicArn --output text)
awslocal sns subscribe --topic-arn "$topic_arn" --protocol sqs --notification-endpoint "$queue_arn" \
  --attributes RawMessageDelivery=true >/dev/null

echo "provisioned: api=${api_id} stage=${STAGE} pool=${pool_id} client=${client_id}"
