#!/usr/bin/env bash
# Narrated end-to-end demo of a running stack. Every business call goes through the API Gateway.
# Needs: bash, curl, jq, aws CLI, python3 and docker compose (resilience acts stop/start two of the
# project's own services). Usage: ./scripts/demo.sh [all|auth|purchase|diagnosis|escalation|resilience]...
# DEMO_PAUSE=1 waits for Enter before every step.
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 1

export AWS_ENDPOINT_URL=${AWS_ENDPOINT_URL_HOST:-http://localhost:4566}
export AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:-us-east-1}
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
EXPERT_USER=expert@growpath.test
EXPERT_PASSWORD='Passw0rd!Expert'
DEMO_PASSWORD='Passw0rd!Demo'
STAGE=v1
SERVICE_PORTS="commerce:8001 entitlement:8002 plant-care:8003 expert-consultation:8004"
READY_TIMEOUT=${READY_TIMEOUT:-120}
EVENT_TIMEOUT=${EVENT_TIMEOUT:-30}
STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  BOLD=$'\e[1m' DIM=$'\e[2m' RED=$'\e[31m' GREEN=$'\e[32m' YELLOW=$'\e[33m' CYAN=$'\e[36m' RESET=$'\e[0m'
else
  BOLD='' DIM='' RED='' GREEN='' YELLOW='' CYAN='' RESET=''
fi

TMP=$(mktemp -d)
STOPPED=""
VERBOSE=1
CODE='' BODY='' HEADERS='' ELAPSED=''
TOKEN='' EXPERT_TOKEN='' USER_SUB='' DEMO_USER=''
ORDER_ID='' JOURNAL_ID='' UPDATE_ID='' ESCALATION_ID='' TICKET_ID='' TICKET_JSON=''

dc() { docker compose --env-file "$ROOT/.env" "$@"; }
aws_() { aws --no-cli-pager "$@"; }

# ---------------------------------------------------------------- output

act() { printf '\n%s%s=== %s ===%s\n' "$BOLD" "$CYAN" "$*" "$RESET"; }
say() { printf '%s  %s%s\n' "$YELLOW" "$*" "$RESET"; }
ok() { printf '  %sok%s    %s\n' "$GREEN" "$RESET" "$*"; }
info() { printf '  %s%s%s\n' "$DIM" "$*" "$RESET"; }

pause() {
  [ "${DEMO_PAUSE:-0}" = 1 ] || return 0
  printf '%s  [Enter to continue]%s' "$DIM" "$RESET"
  read -r _ || true
}

step() {
  pause
  printf '\n%s-- %s%s\n' "$BOLD" "$*" "$RESET"
}

# Dies with the last response so a failed step explains itself.
die() {
  printf '  %sFAIL%s  %s\n' "$RED" "$RESET" "$*" >&2
  if [ -n "$CODE" ]; then
    printf '        last response: HTTP %s %s\n' "$CODE" "$(printf '%s' "$BODY" | head -c 600)" >&2
  fi
  exit 1
}

# expect <what> <expected, or several separated by spaces> <actual>
expect() {
  case " $2 " in
    *" $3 "*) ok "$1: $3" ;;
    *) die "$1: expected $2, got $3" ;;
  esac
}

truncate_token() { printf '%s... (%s chars)' "${1:0:24}" "${#1}"; }

cleanup() {
  local service
  for service in $STOPPED; do
    printf '\n%scleanup: starting %s again%s\n' "$YELLOW" "$service" "$RESET" >&2
    dc start "$service" >/dev/null 2>&1
  done
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# ---------------------------------------------------------------- HTTP helpers

# call <method> <url> [curl args]: sets CODE, BODY, HEADERS and ELAPSED (seconds).
call() {
  local method=$1 url=$2 result
  shift 2
  result=$(curl -s -m "${CURL_TIMEOUT:-90}" -X "$method" -D "$TMP/headers" -o "$TMP/body" \
    -w '%{http_code} %{time_total}' "$url" "$@") || result='000 0'
  CODE=${result%% *}
  ELAPSED=${result##* }
  BODY=$(cat "$TMP/body" 2>/dev/null)
  HEADERS=$(tr -d '\r' <"$TMP/headers" 2>/dev/null)
}

header() { printf '%s\n' "$HEADERS" | grep -i "^$1:" | head -1 | cut -d' ' -f2-; }

# gw <method> <path under /v1> <token or empty> [curl args]
gw() {
  local method=$1 path=$2 token=$3 args=(-H 'Accept: application/json')
  shift 3
  [ -n "$token" ] && args+=(-H "Authorization: $token")
  call "$method" "$BASE/$path" "${args[@]}" "$@"
  if [ "$VERBOSE" = 1 ]; then
    printf '  %s%s /v1/%s -> %s (%ss) correlation-id=%s%s\n' \
      "$DIM" "$method" "$path" "$CODE" "$ELAPSED" "$(header x-correlation-id)" "$RESET"
  fi
}

# gwj <method> <path> <token> <json> [curl args]
gwj() {
  local method=$1 path=$2 token=$3 json=$4
  shift 4
  gw "$method" "$path" "$token" -H 'Content-Type: application/json' -d "$json" "$@"
}

new_uuid() { python3 -c 'import uuid; print(uuid.uuid4())'; }

# poll <timeout seconds> <what> <function>: the function succeeds when the event has arrived.
poll() {
  local timeout=$1 what=$2 started=$SECONDS
  shift 2
  VERBOSE=0
  printf '  waiting for %s' "$what"
  until "$@"; do
    if [ $((SECONDS - started)) -ge "$timeout" ]; then
      VERBOSE=1
      printf ' timed out after %ss\n' "$timeout"
      return 1
    fi
    printf '.'
    sleep 1
  done
  VERBOSE=1
  printf ' done in %ss\n' $((SECONDS - started))
}

jwt_claims() {
  python3 -I -c 'import base64, json, sys
payload = sys.argv[1].split(".")[1]
print(json.dumps(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))))' "$1"
}

# A 64x64 green PNG, large enough for a real vision model to accept it as an image.
make_sample_png() {
  python3 -I -c 'import struct, sys, zlib
def chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
size = 64
rows = b"".join(b"\0" + bytes(v for x in range(size) for v in (40 + y, 120 + x, 50)) for y in range(size))
sys.stdout.buffer.write(b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))'
}

day_offset() { date -u -v"$1"d +%F 2>/dev/null || date -u -d "$1 days" +%F; }

money() { awk -v cents="$1" 'BEGIN { printf "%.2f", cents / 100 }'; }

# trace <correlation id>: every JSON log line of the stack that carries the id, in time order.
trace() {
  dc logs --no-color --no-log-prefix --since "$STARTED_AT" 2>/dev/null | jq -Rrn --arg id "$1" '
    [inputs | fromjson? | select(.correlation_id == $id)] | sort_by(.timestamp)[]
    | .timestamp[11:23] + "  " + ((.service + "                    ")[0:20]) + .message
      + (if .event_type then "  event_type=" + .event_type else "" end)
      + (if .call then "  call=" + .call else "" end)
      + (if .status != null then "  status=" + (.status | tostring) else "" end)'
}

show_trace() {
  local id=$1 lines
  lines=$(trace "$id")
  printf '%s\n' "$lines" | sed 's/^/    /'
  printf '  services that logged this id: %s\n' \
    "$(dc logs --no-color --no-log-prefix --since "$STARTED_AT" 2>/dev/null | jq -Rrn --arg id "$id" \
      '[inputs | fromjson? | select(.correlation_id == $id) | .service] | unique | join(", ")')"
}

# ---------------------------------------------------------------- stack helpers

ready_code() { curl -s -o /dev/null -m 3 -w '%{http_code}' "http://127.0.0.1:$1/health/ready"; }

service_ready() { [ "$(ready_code "$1")" = 200 ]; }

wait_ready() {
  poll "$READY_TIMEOUT" "$1 /health/ready" service_ready "$2" || die "$1 did not become ready"
}

stop_service() {
  dc stop "$1" >/dev/null 2>&1 || die "cannot stop $1"
  STOPPED="$STOPPED $1"
  ok "docker compose stop $1"
}

start_service() {
  dc start "$1" >/dev/null 2>&1 || die "cannot start $1"
  STOPPED=${STOPPED/ $1/}
  ok "docker compose start $1"
}

queue_url() { aws_ sqs get-queue-url --queue-name "$1" --query QueueUrl --output text; }

queue_depth() {
  aws_ sqs get-queue-attributes --queue-url "$(queue_url "$1")" \
    --attribute-names ApproximateNumberOfMessages --query Attributes.ApproximateNumberOfMessages --output text
}

# ---------------------------------------------------------------- shared steps

resolve_gateway() {
  POOL_ID=$(aws_ cognito-idp list-user-pools --max-results 10 --query 'UserPools[?Name==`growpath`].Id|[0]' --output text)
  CLIENT_ID=$(aws_ cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" --query 'UserPoolClients[0].ClientId' --output text)
  API_ID=$(aws_ apigateway get-rest-apis --query 'items[?name==`growpath-gateway`].id|[0]' --output text)
  [ -n "$API_ID" ] && [ "$API_ID" != None ] || die "the gateway is not provisioned on LocalStack"
  BASE="$AWS_ENDPOINT_URL/restapis/$API_ID/$STAGE/_user_request_/v1"
}

login() {
  aws_ cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH --client-id "$CLIENT_ID" \
    --auth-parameters "USERNAME=$1,PASSWORD=$2" --query AuthenticationResult.IdToken --output text
}

# A new user per run keeps the demo repeatable: the first-purchase discount and the quotas start clean.
ensure_user() {
  [ -n "$TOKEN" ] && return 0
  DEMO_USER="demo-$(date +%s)-$RANDOM@growpath.test"
  aws_ cognito-idp admin-create-user --user-pool-id "$POOL_ID" --username "$DEMO_USER" \
    --message-action SUPPRESS >/dev/null || die "cannot create the demo user"
  aws_ cognito-idp admin-set-user-password --user-pool-id "$POOL_ID" --username "$DEMO_USER" \
    --password "$DEMO_PASSWORD" --permanent || die "cannot set the demo user password"
  # Every user is in `users`: see Known limitations in the README.
  aws_ cognito-idp admin-add-user-to-group --user-pool-id "$POOL_ID" --username "$DEMO_USER" \
    --group-name users || die "cannot add the demo user to the users group"
  TOKEN=$(login "$DEMO_USER" "$DEMO_PASSWORD")
  [ -n "$TOKEN" ] && [ "$TOKEN" != None ] || die "login failed for the demo user"
  USER_SUB=$(jwt_claims "$TOKEN" | jq -r .sub)
  ok "created and logged in $DEMO_USER (token $(truncate_token "$TOKEN"))"
}

ensure_expert() {
  [ -n "$EXPERT_TOKEN" ] && return 0
  EXPERT_TOKEN=$(login "$EXPERT_USER" "$EXPERT_PASSWORD")
  [ -n "$EXPERT_TOKEN" ] && [ "$EXPERT_TOKEN" != None ] || die "login failed for the expert"
  ok "logged in $EXPERT_USER (token $(truncate_token "$EXPERT_TOKEN"))"
}

show_entitlement() {
  gw GET me/entitlement "$TOKEN"
  expect "GET /me/entitlement" 200 "$CODE"
  jq -r '"    tier=\(.tier)  ai_quota_today=\(.ai_quota_today.used)/\(.ai_quota_today.limit)  expert_quota_30_days=\(.expert_quota_30_days.used)/\(.expert_quota_30_days.limit)"' <<<"$BODY"
}

quota_left_in_body() {
  [ "$CODE" = 200 ] && [ "$(jq '.expert_quota_30_days.limit - .expert_quota_30_days.used' <<<"$BODY")" -gt 0 ]
}

expert_quota_left() {
  gw GET me/entitlement "$TOKEN"
  quota_left_in_body
}

# buy <item id>: sets ORDER_ID.
buy() {
  local key
  key=$(new_uuid)
  gwj POST orders "$TOKEN" "{\"item_id\":\"$1\"}" -H "Idempotency-Key: $key"
  expect "POST /orders" 201 "$CODE"
  ORDER_ID=$(jq -r .id <<<"$BODY")
  printf '    list price %s, discount %s, paid %s\n' "$(money "$(jq .list_price_cents <<<"$BODY")")" \
    "$(money "$(jq .discount_cents <<<"$BODY")")" "$(money "$(jq .total_paid_cents <<<"$BODY")")"
  LAST_KEY=$key
}

journal_for_order() {
  gw GET journals "$TOKEN"
  [ "$CODE" = 200 ] || return 1
  JOURNAL_ID=$(jq -r --arg order "$ORDER_ID" '[.[] | select(.order_id == $order)][0].id // empty' <<<"$BODY")
  [ -n "$JOURNAL_ID" ]
}

load_catalog() {
  gw GET items "$TOKEN"
  expect "GET /items" 200 "$CODE"
  KIT_ID=$(jq -r '[.[] | select(.first_purchase_discount_pct != null)][0].id' <<<"$BODY")
  PRICIEST_ID=$(jq -r '[.[] | select(.item_type == "pack")] | max_by(.price_cents) | .id' <<<"$BODY")
  jq -r '.[] | "    \(.name): \(.price_cents / 100)" + (if .first_purchase_discount_pct then " (first purchase -\(.first_purchase_discount_pct)%)" else "" end)' <<<"$BODY"
}

ensure_journal() {
  [ -n "$JOURNAL_ID" ] && return 0
  ensure_user
  step "Purchase a starter pack (needed by the next steps)"
  load_catalog
  buy "$KIT_ID"
  poll "$EVENT_TIMEOUT" "order.placed to reach plant-care (journal draft)" journal_for_order \
    || die "no journal draft appeared: check queue plant-care-order-placed and its DLQ"
}

ensure_expert_quota() {
  local attempt
  for attempt in 1 2 3; do
    show_entitlement
    quota_left_in_body && return 0
    say "Starter has an expert limit of 0; experts need the Hobbyist tier (40.00 in 30 days). Buying the most expensive pack."
    load_catalog >/dev/null
    buy "$PRICIEST_ID"
    poll "$EVENT_TIMEOUT" "order.placed to reach entitlement (tier recalculated)" expert_quota_left && continue
  done
  expert_quota_left || die "the expert quota stays 0: check queue entitlement-order-placed and its DLQ"
}

# new_update: a fresh update with the two required photos; sets UPDATE_ID.
new_update() {
  local body kind
  ensure_journal
  UPDATE_ID=$(new_uuid)
  body=$(jq -n --arg id "$UPDATE_ID" --arg watered "$(day_offset -2)" '{
    id: $id, last_watered_on: $watered, watering_interval_days: 3, drainage: "poor",
    lighting: "low", finger_test: "wet", pot_weight: "heavier", sensor_reading: 82.5}')
  gwj POST "journals/$JOURNAL_ID/updates" "$TOKEN" "$body"
  expect "POST /journals/{id}/updates" 201 "$CODE"
  for kind in overview soil; do upload_photo "$kind"; done
}

upload_photo() {
  local url put_code
  gwj POST "updates/$UPDATE_ID/photos" "$TOKEN" "{\"kind\":\"$1\"}"
  expect "POST /updates/{id}/photos ($1)" "200 201" "$CODE"
  url=$(jq -r .upload_url <<<"$BODY")
  printf '    presigned PUT to %s?<signature>\n' "${url%%\?*}"
  put_code=$(curl -s -o /dev/null -m 30 -w '%{http_code}' -X PUT -H 'Content-Type: image/png' \
    --data-binary @"$TMP/sample.png" "$url")
  expect "PUT photo straight to S3 ($1)" 200 "$put_code"
}

diagnosis_summary() {
  gw GET "updates/$UPDATE_ID/diagnosis" "$TOKEN"
  expect "GET /updates/{id}/diagnosis" 200 "$CODE"
  jq -r '"    status=\(.status)  model_effort=\(.model_effort)  has_dominant_candidate=\(.has_dominant_candidate)  escalation_offered=\(.escalation_offered)"' <<<"$BODY"
  jq -r '.candidates[] | "    candidate \(.rank): \(.cause)"' <<<"$BODY"
}

ticket_for_update() {
  local after=${1:-} page ids id
  while :; do
    gw GET "expert/tickets?limit=100${after:+&after=$(jq -rn --arg c "$after" '$c|@uri')}" "$EXPERT_TOKEN"
    [ "$CODE" = 200 ] || return 1
    page=$BODY
    ids=$(jq -r --arg since "$ESCALATED_AT" '.items[] | select(.created_at[0:19] >= $since) | .id' <<<"$page")
    for id in $ids; do
      gw GET "expert/tickets/$id" "$EXPERT_TOKEN"
      [ "$CODE" = 200 ] || continue
      if [ "$(jq -r .context.update.id <<<"$BODY")" = "$UPDATE_ID" ]; then
        TICKET_ID=$id
        TICKET_JSON=$BODY
        return 0
      fi
    done
    after=$(jq -r '.next_cursor // empty' <<<"$page")
    [ -n "$after" ] || return 1
  done
}

escalation_status_is() {
  gw GET "updates/$UPDATE_ID/escalation" "$TOKEN"
  [ "$CODE" = 200 ] && [ "$(jq -r .status <<<"$BODY")" = "$1" ]
}

# escalate <question>: sets ESCALATION_ID and ESCALATED_AT.
escalate() {
  ESCALATED_AT=$(date -u -v-60S +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '60 seconds ago' +%Y-%m-%dT%H:%M:%S)
  gwj POST "updates/$UPDATE_ID/escalation" "$TOKEN" "$(jq -n --arg q "$1" '{user_question: $q}')"
  expect "POST /updates/{id}/escalation" 201 "$CODE"
  ESCALATION_ID=$(jq -r .id <<<"$BODY")
  ESCALATION_CORRELATION=$(header x-correlation-id)
  printf '    escalation %s status=%s\n' "$ESCALATION_ID" "$(jq -r .status <<<"$BODY")"
}

# ---------------------------------------------------------------- acts

act_preflight() {
  act "0. Preflight: services, gateway, model provider"
  local entry provider
  for entry in $SERVICE_PORTS; do
    wait_ready "${entry%%:*}" "${entry##*:}"
  done
  resolve_gateway
  ok "gateway: REST API $API_ID, stage $STAGE, base $BASE"
  provider=$(dc exec -T plant-care printenv PLANT_CARE_MODEL_PROVIDER 2>/dev/null | tr -d '\r')
  if [ -n "$provider" ]; then
    ok "model provider (PLANT_CARE_MODEL_PROVIDER): $provider"
    [ "$provider" = mock ] && info "mock is deterministic; set PLANT_CARE_MODEL_PROVIDER=openrouter in .env for live answers"
  else
    info "model provider: unknown (docker compose exec was not possible)"
  fi
  make_sample_png >"$TMP/sample.png"
}

act_auth() {
  act "1. Authentication and identity at the gateway"
  step "No token and a garbage token are refused by the Cognito authorizer"
  gw GET items ""
  expect "no token" 401 "$CODE"
  gw GET items "garbage"
  expect "garbage token" 401 "$CODE"

  step "Log in as a new user and as the expert; both come from Cognito on LocalStack"
  ensure_user
  ensure_expert
  say "The token is a Cognito ID token. These are its claims (the gateway reads sub and cognito:groups):"
  jwt_claims "$TOKEN" | jq '{sub, "cognito:groups", token_use, "cognito:username"}'
  jwt_claims "$EXPERT_TOKEN" | jq -c '{sub, "cognito:groups"}'

  step "A valid token passes; X-User-Id and X-User-Groups are set by the gateway, not by the client"
  gw GET items "$TOKEN"
  expect "valid token" 200 "$CODE"
  gw GET expert/tickets "$TOKEN"
  expect "user without the experts group" 403 "$CODE"
  gw GET expert/tickets "$TOKEN" -H 'X-User-Groups: experts'
  expect "user forges X-User-Groups: experts" 403 "$CODE"
  gw GET me/entitlement "$TOKEN" -H 'X-User-Id: 00000000-0000-0000-0000-000000000000'
  expect "user forges X-User-Id" 200 "$CODE"
  expect "entitlement answers for the sub of the token" "$USER_SUB" "$(jq -r .user_id <<<"$BODY")"
  gw GET expert/tickets "$EXPERT_TOKEN"
  expect "expert in the experts group" 200 "$CODE"
  gwj POST quotas/ai/reservations "$TOKEN" '{}'
  expect "internal quota API is not routed" 403 "$CODE"

  step "Rate limit: declared at the gateway stage, not enforced by LocalStack"
  aws_ apigateway get-stage --rest-api-id "$API_ID" --stage-name "$STAGE" \
    --query 'methodSettings."*/*".{throttlingRateLimit:throttlingRateLimit,throttlingBurstLimit:throttlingBurstLimit}' --output json
  say "Limit is 2 requests per second. LocalStack stores it but does not apply it, so a burst of 15 still passes:"
  VERBOSE=0
  for _ in $(seq 15); do gw GET items "$TOKEN"; printf '%s\n' "$CODE"; done | sort | uniq -c | sed 's/^/    /'
  VERBOSE=1
  info "On real AWS API Gateway the same stage settings return 429. There is no service-side 429 yet (README, Known limitations)."
}

act_purchase() {
  act "2. Purchase through commerce, events to entitlement and plant-care"
  ensure_user
  step "Catalog and first purchase (20% first-purchase discount, Idempotency-Key header)"
  load_catalog
  buy "$KIT_ID"
  first_order=$ORDER_ID
  say "Same Idempotency-Key again: the same order is returned (200), nothing is charged twice."
  gwj POST orders "$TOKEN" "{\"item_id\":\"$KIT_ID\"}" -H "Idempotency-Key: $LAST_KEY"
  expect "repeat with the same key" 200 "$CODE"
  expect "same order id" "$first_order" "$(jq -r .id <<<"$BODY")"

  step "commerce published order.placed to SNS; plant-care consumed it and created the journal draft"
  poll "$EVENT_TIMEOUT" "order.placed to reach plant-care (journal draft)" journal_for_order \
    || die "no journal draft appeared: check queue plant-care-order-placed and its DLQ"
  gw GET "journals/$JOURNAL_ID" "$TOKEN"
  jq -r '"    journal \(.id)  species=\(.species)  planted_on=\(.planted_on)"' <<<"$BODY"

  step "entitlement consumed the same event: tier is a function of the last 30 days of orders"
  show_entitlement
  ensure_expert_quota
}

act_diagnosis() {
  act "3. Journal update, photos in S3, AI diagnosis with quota reserve and commit"
  ensure_user
  ensure_journal
  step "Planting date and checkpoints (computed when read, not stored)"
  gwj PUT "journals/$JOURNAL_ID/planting" "$TOKEN" "{\"planted_on\":\"$(day_offset -10)\"}"
  expect "PUT /journals/{id}/planting" 200 "$CODE"
  jq -r '"    planted_on=\(.planted_on)  checkpoints=\(.checkpoints | length)"' <<<"$BODY"

  step "New update with the questionnaire, then the two required photos by presigned PUT to S3"
  new_update
  gw GET "updates/$UPDATE_ID/photos" "$TOKEN"
  jq -r '"    photos=\(.photos | length)  missing_required=\(.missing_required)"' <<<"$BODY"

  step "Run the diagnosis: AI quota is reserved in entitlement, the model is called, the reservation is committed or released"
  say "Creating the update already reserved one AI quota unit (used=1 below); the diagnosis runs against that reservation."
  show_entitlement
  gw POST "updates/$UPDATE_ID/diagnosis" "$TOKEN"
  expect "POST /updates/{id}/diagnosis" "200 201" "$CODE"
  if [ "$(jq -r .status <<<"$BODY")" = awaiting_clarification ]; then
    say "The model asked: $(jq -r .clarifying_question <<<"$BODY")"
    if [ "$(jq -r .roots_photo_requested <<<"$BODY")" = true ]; then upload_photo roots_drainage; fi
    gwj POST "updates/$UPDATE_ID/diagnosis/clarification" "$TOKEN" \
      '{"answer":"Yes, the soil stays wet for several days after watering."}'
    expect "POST .../diagnosis/clarification" "200 201" "$CODE"
  fi
  diagnosis_summary
  if [ "$(jq -r .status <<<"$BODY")" = degraded ]; then
    say "The model gave no usable answer: the diagnosis degraded and the AI quota reservation was released, so nothing is charged."
  else
    say "The reservation was committed: one unit of AI quota is spent."
  fi
  show_entitlement
}

act_escalation() {
  act "4. Escalation to an expert: events across three services"
  ensure_user
  ensure_expert
  [ -n "$UPDATE_ID" ] || { ensure_journal; new_update; }
  ensure_expert_quota

  step "The user asks an expert: plant-care reserves expert quota, then publishes escalation.requested"
  escalate "Are the roots healthy? The soil stays wet for days."
  step "expert-consultation consumed the event and opened a ticket; the expert sees it in the queue"
  poll "$EVENT_TIMEOUT" "the ticket in the expert queue" ticket_for_update \
    || die "no ticket appeared: check queue expert-escalation-requested and its DLQ"
  jq -r '"    ticket \(.id)  status=\(.status)  expires_at=\(.expires_at)\n    context: species=\(.context.species)  user_question=\(.context.user_question)  photos=\(.photos | length)"' <<<"$TICKET_JSON"

  step "One correlation id across services: gateway -> plant-care -> SNS -> expert-consultation -> SNS -> plant-care, entitlement"
  poll "$EVENT_TIMEOUT" "ticket.created to reach plant-care" escalation_status_is ticket_open \
    || die "plant-care did not see ticket.created: check queue plant-care-ticket-events and its DLQ"
  sleep 2
  info "docker compose logs | jq 'select(.correlation_id == \"$ESCALATION_CORRELATION\")'"
  show_trace "$ESCALATION_CORRELATION"
  info "commerce is absent on purpose: this flow does not touch it."

  step "The expert answers; ticket.answered flows back and the user sees the answer"
  gwj POST "expert/tickets/$TICKET_ID/answer" "$EXPERT_TOKEN" \
    '{"body":"Let the soil dry between waterings and check the roots: if they are brown and soft, repot into fresh mix with drainage."}'
  expect "POST /expert/tickets/{id}/answer" 201 "$CODE"
  answer_correlation=$(header x-correlation-id)
  poll "$EVENT_TIMEOUT" "ticket.answered to reach plant-care" escalation_status_is answered \
    || die "the answer did not arrive: check queue plant-care-ticket-events and its DLQ"
  jq -r '"    status=\(.status)\n    answer: \(.answer_text)"' <<<"$BODY"
  sleep 2
  say "Correlation id of the answering request:"
  show_trace "$answer_correlation"
  say "entitlement committed the expert reservation on ticket.created:"
  show_entitlement
}

act_resilience() {
  act "5. Resilience: dependency down, broker decoupling, dead-letter queue"
  ensure_user
  ensure_expert
  ensure_journal
  ensure_expert_quota
  resilience_entitlement_down
  resilience_expert_down
  resilience_dead_letter
}

resilience_entitlement_down() {
  local n
  step "5a. entitlement is down: updates are still accepted, the diagnosis fails fast with 503 + Retry-After"
  stop_service entitlement
  say "Submitting an update needs no quota decision: it is stored with a diagnosis_pending fallback."
  new_update
  gw GET "updates/$UPDATE_ID/diagnosis" "$TOKEN"
  jq -r '"    status=\(.status)"' <<<"$BODY"
  say "Running the diagnosis needs entitlement. plant-care retries with jitter, then the circuit breaker opens after 5 failed calls and rejects at once:"
  for n in 1 2 3 4 5 6; do
    gw POST "updates/$UPDATE_ID/diagnosis" "$TOKEN"
    expect "diagnosis attempt $n" 503 "$CODE"
  done
  printf '    Retry-After: %s\n' "$(header retry-after)"
  jq -c '{status, title, detail, correlation_id}' <<<"$BODY" | sed 's/^/    /'
  say "plant-care log lines about the breaker:"
  dc logs --no-color --no-log-prefix plant-care --since "$STARTED_AT" 2>/dev/null \
    | jq -Rr 'fromjson? | select(.message | test("circuit breaker|rejected by the open breaker")) | "    " + .timestamp[11:23] + "  " + .message' | head -5
  say "The user's own data is intact while entitlement is down:"
  gw GET "journals/$JOURNAL_ID/updates" "$TOKEN"
  expect "GET /journals/{id}/updates" 200 "$CODE"
  start_service entitlement
  wait_ready entitlement 8002
  say "The breaker is half-open for one probe after its reset timeout; the diagnosis works again:"
  diagnosis_after_recovery() { gw POST "updates/$UPDATE_ID/diagnosis" "$TOKEN"; [ "$CODE" = 200 ] || [ "$CODE" = 201 ]; }
  poll 60 "the diagnosis to succeed" diagnosis_after_recovery || die "the diagnosis did not recover"
  diagnosis_summary
}

resilience_expert_down() {
  local depth
  step "5b. expert-consultation is down: the escalation is accepted, the event waits in SQS"
  stop_service expert-consultation
  escalate "Broker demo: please look at this while you are offline."
  expect "escalation status" requested "$(jq -r .status <<<"$BODY")"
  say "The event is buffered in the subscriber queue (SNS fan-out to SQS):"
  printf '    expert-escalation-requested: %s message(s)\n' "$(queue_depth expert-escalation-requested)"
  say "plant-care re-sends an unconfirmed escalation every 30 s (repeater): wait for a duplicate (at-least-once)."
  two_messages_queued() { [ "$(queue_depth expert-escalation-requested)" -ge 2 ]; }
  if poll 60 "the repeater to publish a duplicate" two_messages_queued; then
    depth=$(queue_depth expert-escalation-requested)
    printf '    expert-escalation-requested: %s message(s), same escalation id\n' "$depth"
  fi
  start_service expert-consultation
  wait_ready expert-consultation 8004
  poll "$EVENT_TIMEOUT" "the ticket to appear after the restart" ticket_for_update \
    || die "no ticket appeared after expert-consultation came back"
  jq -r '"    ticket \(.id)  status=\(.status)"' <<<"$TICKET_JSON"
  poll "$EVENT_TIMEOUT" "plant-care to see ticket.created" escalation_status_is ticket_open \
    || die "plant-care did not see ticket.created"
  ok "escalation status: ticket_open"
  sleep 3
  say "The repeated copies carry the escalation id as correlation id. They create no second ticket: the existing one is re-announced and plant-care ignores the duplicate ticket.created:"
  show_trace "$ESCALATION_ID"
}

dead_letter_arrived() {
  local messages
  messages=$(aws_ sqs receive-message --queue-url "$DLQ_URL" --max-number-of-messages 10 \
    --visibility-timeout 1 --query 'Messages[].{id:MessageId,body:Body,handle:ReceiptHandle}' --output json 2>/dev/null)
  DLQ_HIT=$(jq -c --arg marker "$POISON_MARKER" '[.[]? | select(.body | contains($marker))][0] // empty' <<<"$messages")
  [ -n "$DLQ_HIT" ]
}

resilience_dead_letter() {
  local message_id
  step "5c. Poison message: it cannot be handled, so SQS moves it to the DLQ after maxReceiveCount (2)"
  POISON_MARKER="poison-$(new_uuid)"
  DLQ_URL=$(queue_url plant-care-order-placed-dlq)
  message_id=$(aws_ sqs send-message --queue-url "$(queue_url plant-care-order-placed)" \
    --message-body "{\"marker\":\"$POISON_MARKER\"}" --query MessageId --output text)
  ok "sent a message without event_type to plant-care-order-placed (id $message_id)"
  poll 60 "the message to reach plant-care-order-placed-dlq" dead_letter_arrived \
    || die "the poison message never reached the DLQ"
  jq -c '{body}' <<<"$DLQ_HIT" | sed 's/^/    /'
  say "plant-care logged each failed attempt and left the message in the queue:"
  show_trace "$message_id"
  aws_ sqs delete-message --queue-url "$DLQ_URL" --receipt-handle "$(jq -r .handle <<<"$DLQ_HIT")" >/dev/null 2>&1
  info "the demo message was deleted from the DLQ again"
}

# ---------------------------------------------------------------- main

usage() {
  printf 'Usage: %s [all|auth|purchase|diagnosis|escalation|resilience]...\n' "$0"
  exit "${1:-0}"
}

main() {
  local selected=${*:-all} name
  for name in $selected; do
    case "$name" in all | auth | purchase | diagnosis | escalation | resilience) ;; -h | --help) usage ;; *) usage 2 ;; esac
  done
  for tool in curl jq aws python3 docker; do
    command -v "$tool" >/dev/null || die "$tool is required"
  done
  [ -f "$ROOT/.env" ] || die ".env not found: run make up first"

  act_preflight
  for name in $selected; do
    case "$name" in
      all) act_auth; act_purchase; act_diagnosis; act_escalation; act_resilience ;;
      auth) act_auth ;;
      purchase) act_purchase ;;
      diagnosis) act_diagnosis ;;
      escalation) act_escalation ;;
      resilience) act_resilience ;;
    esac
  done
  printf '\n%s%sdemo: finished%s\n' "$BOLD" "$GREEN" "$RESET"
}

main "$@"
