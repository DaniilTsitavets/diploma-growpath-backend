#!/usr/bin/env bash
# Prints the LocalStack ids a browser client needs, ready to paste into its .env.local.
# The api id and the Cognito client id change every time LocalStack is recreated.
set -euo pipefail

export AWS_ENDPOINT_URL=${AWS_ENDPOINT_URL_HOST:-http://localhost:4566}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test

api_id=$(aws apigateway get-rest-apis --query "items[?name=='growpath-gateway'].id|[0]" --output text)
pool_id=$(aws cognito-idp list-user-pools --max-results 20 --query "UserPools[?Name=='growpath'].Id|[0]" --output text)
if [ "$api_id" = None ] || [ "$pool_id" = None ]; then
  echo "gateway or user pool not found: is the stack up (make up)?" >&2
  exit 1
fi
client_id=$(aws cognito-idp list-user-pool-clients --user-pool-id "$pool_id" \
  --query "UserPoolClients[0].ClientId" --output text)

cdn_domain=$(aws cloudfront list-distributions --query "DistributionList.Items[?Comment=='growpath spa and api'].DomainName|[0]" --output text 2>/dev/null || echo None)

cat <<EOF
# Local Vite dev server: proxy /v1 to this target (same origin as the page, so no CORS).
VITE_GATEWAY_PROXY_TARGET=http://localhost:4566/restapis/${api_id}/v1/_user_request_
# Direct URL of the gateway, for a client that does not proxy (needs CORS, which the gateway does not set).
VITE_API_BASE_URL=http://localhost:4566/restapis/${api_id}/v1/_user_request_/v1
VITE_COGNITO_ENDPOINT=http://localhost:4566/
VITE_COGNITO_USER_POOL_ID=${pool_id}
VITE_COGNITO_CLIENT_ID=${client_id}
# Catalog images: <base>/<item slug>.webp (a missing file answers 404). Not part of the API.
VITE_MEDIA_BASE_URL=${AWS_ENDPOINT_URL}/growpath-media
EOF

if [ "$cdn_domain" != None ] && [ -n "$cdn_domain" ]; then
  echo "# Frontend through CloudFront, after make deploy-frontend: http://${cdn_domain}:4566/" >&2
fi
