# Deployment facts for the platform team

What the four services need to run on Kubernetes (EKS, Argo CD, one unified Helm chart). This is the
source of truth on the backend side: the chart and the values files live in the deploy repository
and follow this document. Facts below come from the code (`app/config.py`, `app/api/health.py`,
`infra/localstack/init/ready.d/01-provision.sh`) unless marked *estimate*.

## Containers

| Item | Value |
|---|---|
| Image | one per service, built from the repository root: `docker build -f services/<service>/Dockerfile .` |
| Container port | `8000` (HTTP), the same for every service |
| User | non-root, numeric UID `10001` (set in the Dockerfile; `runAsNonRoot` needs a numeric UID) |
| Entry | the image `CMD` migrates and then starts `uvicorn`; on Kubernetes override it (see below) |
| Writable paths | none required; mount an `emptyDir` on `/tmp` if `readOnlyRootFilesystem` is on |
| Architecture | the images are built for the builder's architecture; build `linux/amd64` (and `linux/arm64` if Karpenter may pick Graviton) |

### Probes

| Probe | Path | Notes |
|---|---|---|
| liveness | `GET /health/live` | no dependencies, always 200 while the process is up |
| readiness | `GET /health/ready` | `SELECT 1` on the database and, for services that consume, `GetQueueAttributes` on each queue; 503 when either fails. commerce checks the database only |
| startup | `GET /health/live` | the app is up within seconds once migrations are done; allow roughly 60 s |

### Migrations and seeds

Alembic migrations are idempotent and must run once per rollout, before the new pods start:

```
alembic upgrade head                              # every service
python -m app.seed                                # commerce only: catalog items and labels (idempotent)
```

The image `CMD` runs the migration and then `uvicorn` in one shell command, which is what
docker-compose uses locally. On Kubernetes override the command: run the lines above in a Job (one
per release) and start the Deployment with:

```
uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000
```

plant-care needs a one-off knowledge-base import after its first start (without it
`/v1/plants/{slug}` answers 404 and the care chat finds nothing): `python -m app.knowledge_import`
(needs outbound HTTPS to `www.plantsolve.com`, the only host it calls, and the database). It is a
manual Job, not part of every rollout.

### Replicas

Every replica runs its own queue consumer and its own republisher or maintenance sweep inside the
process. That is safe: SQS consumers compete for messages, handlers are idempotent by business key
and a conditional `UPDATE`, and event publication is at-least-once (a duplicate is possible and
harmless). Two or more replicas per service are fine.

## Routes (what the API gateway and the ALB must forward)

The gateway sets `X-User-Id` (`sub`), `X-User-Groups` (`cognito:groups`) and `X-Correlation-Id`
(request id), overwriting client values. Services trust them, so the pods must be reachable only from
the ALB (plus the internal calls below). The paths below are what arrives at the pod.

| Service | Paths | Public (no JWT) |
|---|---|---|
| commerce | `/v1/items`, `/v1/items/{slug}`, `/v1/orders` (POST) | `GET /v1/items`, `GET /v1/items/{slug}` |
| plant-care | `/v1/plants/{slug}`, `/v1/journals`, `/v1/journals/*`, `/v1/updates/*` | `GET /v1/plants/{slug}` |
| expert-consultation | `/v1/expert/tickets`, `/v1/expert/tickets/*`, `/v1/me` (GET, exact), `/v1/admin/users`, `/v1/admin/users/*` | none |
| entitlement | `/v1/me/entitlement` (GET, exact) | none |

Notes for the Ingress:

- `/v1/me` (expert-consultation) and `/v1/me/entitlement` (entitlement) share a prefix: use exact
  paths, not prefixes.
- entitlement also serves internal endpoints under `/v1/quotas/ai/reservations`,
  `/v1/quotas/expert/reservations` and `/v1/entitlements`. They are **not** behind the gateway; only
  plant-care calls them (`PLANT_CARE_ENTITLEMENT_URL`, the in-cluster Service of entitlement, port 8000
  or whatever the Service exposes). The NetworkPolicy of entitlement must allow plant-care pods in
  addition to the ALB, and the Ingress must not expose those paths.
- Health paths are not routed through the gateway.

## Environment variables

Prefix per service: `COMMERCE_`, `ENTITLEMENT_`, `PLANT_CARE_`, `EXPERT_CONSULTATION_`. Every setting
is an environment variable `<PREFIX><NAME>`; names below omit the prefix. Anything not listed has a
safe default (timeouts, page sizes, TTLs, thresholds; see `app/config.py`).

**No AWS keys.** The code uses the default AWS credential chain, so on EKS it gets credentials from
Pod Identity. Leave `AWS_ENDPOINT_URL` unset (it exists for LocalStack only). Set the region: the
default in code is `us-east-1`, so `<PREFIX>AWS_REGION=eu-north-1` is required for every service.

### Required in every service

| Name | Secret | Meaning |
|---|---|---|
| `DATABASE_URL` | **yes** | `postgresql+psycopg://<role>:<password>@<host>:5432/<db>` (one database and role per service) |
| `AWS_REGION` | no | `eu-north-1` |
| `LOG_LEVEL` | no | default `INFO` |

### Per service

| Service | Name | Secret | Meaning |
|---|---|---|---|
| commerce | `ORDER_PLACED_TOPIC_ARN` | no | SNS topic `order-placed` (publisher) |
| entitlement | `ORDER_PLACED_QUEUE_URL` | no | SQS queue `entitlement-order-placed` |
| entitlement | `TICKET_EVENTS_QUEUE_URL` | no | SQS queue `entitlement-ticket-events` |
| plant-care | `ORDER_PLACED_QUEUE_URL` | no | SQS queue `plant-care-order-placed` |
| plant-care | `TICKET_EVENTS_QUEUE_URL` | no | SQS queue `plant-care-ticket-events` |
| plant-care | `ESCALATION_TOPIC_ARN` | no | SNS topic `escalation-requested` (publisher) |
| plant-care | `ENTITLEMENT_URL` | no | in-cluster URL of entitlement, e.g. `http://entitlement.growpath.svc:8000` |
| plant-care | `MODEL_PROVIDER` | no | `bedrock` in production (`mock` is the default; `openrouter` is for local runs) |
| plant-care | `BEDROCK_MODEL_ID` | no | inference profile id, e.g. `eu.anthropic.claude-haiku-5-5` |
| plant-care | `PHOTOS_BUCKET` | no | photo bucket name (default `growpath-photos`) |
| plant-care | `OPENROUTER_API_KEY` | **yes** | only with `MODEL_PROVIDER=openrouter`; not used in production |
| expert-consultation | `ESCALATION_QUEUE_URL` | no | SQS queue `expert-escalation-requested` |
| expert-consultation | `TICKET_EVENTS_TOPIC_ARN` | no | SNS topic `ticket-events` (publisher) |
| expert-consultation | `COGNITO_USER_POOL_ID` | no | the Cognito user pool id (admin API and `GET /v1/me`) |
| expert-consultation | `PHOTOS_BUCKET` | no | same photo bucket (presigned GET for the expert) |

Optional tuning worth knowing: `PLANT_CARE_DIAGNOSIS_SAMPLES` (parallel model calls per diagnosis,
default 1), `PLANT_CARE_MODEL_TIMEOUT_SECONDS`, `PLANT_CARE_MODEL_BULKHEAD_SIZE`,
`EXPERT_CONSULTATION_TICKET_TTL_SECONDS` (default 259200 = 72 h).

Only `DATABASE_URL` is a secret in production; everything else is configuration. The OpenRouter key is
a secret that production does not set.

## Messaging

Topics are owned by the publisher, every subscriber has its own queue with a dead-letter queue.
Subscriptions use **raw message delivery**; consumers read the `event_type` message attribute, so
the SNS to SQS subscription must keep message attributes.

| Topic (publisher) | Subscriber queue | DLQ | Filter on `event_type` |
|---|---|---|---|
| `order-placed` (commerce) | `entitlement-order-placed` | `order-placed-dlq` | none |
| `order-placed` (commerce) | `plant-care-order-placed` | `plant-care-order-placed-dlq` | none |
| `ticket-events` (expert-consultation) | `entitlement-ticket-events` | `entitlement-ticket-events-dlq` | `ticket.created`, `ticket.cancelled` |
| `ticket-events` (expert-consultation) | `plant-care-ticket-events` | `plant-care-ticket-events-dlq` | none |
| `escalation-requested` (plant-care) | `expert-escalation-requested` | `expert-escalation-requested-dlq` | none |

Queue settings in LocalStack are tuned for a quick demo (`VisibilityTimeout` 1 s, `maxReceiveCount`
2). Production values to use: `VisibilityTimeout` of at least 30 s (above the longest handler),
`maxReceiveCount` 5, long polling is done by the consumer (`ReceiveMessage` wait time 5 s). Each queue
policy must allow `sqs:SendMessage` from the matching topic ARN.

## AWS permissions per service (Pod Identity role)

| Service (ServiceAccount) | Actions | Resources |
|---|---|---|
| commerce | `sns:Publish` | topic `order-placed` |
| entitlement | `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` | queues `entitlement-order-placed`, `entitlement-ticket-events` |
| plant-care | `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` | queues `plant-care-order-placed`, `plant-care-ticket-events` |
| plant-care | `sns:Publish` | topic `escalation-requested` |
| plant-care | `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` (`HeadObject` needs `s3:GetObject`; the presigned PUT is signed with the role) | photo bucket, prefix `journals/*` |
| plant-care | `bedrock:InvokeModel` (the Converse API is authorised by it) | the inference profile and the foundation models it routes to, in every region the profile uses |
| expert-consultation | `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` | queue `expert-escalation-requested` |
| expert-consultation | `sns:Publish` | topic `ticket-events` |
| expert-consultation | `s3:GetObject` | photo bucket, prefix `journals/*` (presigned GET only) |
| expert-consultation | `cognito-idp:AdminCreateUser`, `AdminGetUser`, `AdminListGroupsForUser`, `AdminAddUserToGroup`, `AdminRemoveUserFromGroup`, `AdminDisableUser`, `AdminEnableUser`, `AdminSetUserPassword`, `AdminDeleteUser`, `ListUsers`, `ListUsersInGroup` | the user pool |

If the queues or topics use a customer-managed KMS key, add `kms:Decrypt` and `kms:GenerateDataKey`
for the consumers and publishers. The photo bucket needs CORS for `PUT` from the web client origin;
the client sends exactly the `Content-Type` and `Content-Length` it announced. S3 Vectors (planned,
not built) will add its own actions to plant-care.

ServiceAccount names (fixed by agreement): `commerce`, `entitlement`, `plant-care`,
`expert-consultation`, namespace `growpath`.

## Sizing (*estimate*, not measured)

Nothing has been load-tested; these are starting points for the first deploy, to be tuned from real
metrics.

| Service | CPU request | Memory request = limit |
|---|---|---|
| commerce | 100m | 256Mi |
| entitlement | 100m | 256Mi |
| plant-care | 250m | 512Mi (reads photos into memory for the model: up to 5 MB each) |
| expert-consultation | 100m | 256Mi |

Migration Jobs need about the same as the service. Do not set a CPU limit lower than a few hundred
millicores: startup (imports, migrations) is CPU-bound.

## Not implemented yet (do not expect from the chart)

- Prometheus metrics and alerts: the code exposes none; logs are JSON on stdout with `service`,
  `level`, `correlation_id` and `event`.
- OpenTelemetry traces: the code does not create spans or read `OTEL_*` variables yet; an auto
  instrumentation agent or an init step could be added later.
- Rate limiting (429): declared at the gateway only.
- `HEALTHCHECK` in the Dockerfiles: Kubernetes probes replace it.
