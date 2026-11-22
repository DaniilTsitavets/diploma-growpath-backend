# diploma-growpath-backend

Backend of **GrowPath**, a guided hobby plant-growing platform: a physical starter pack, a multimodal
AI assistant that diagnoses plant problems, and a quota-based escalation path to a live botanist.
Bachelor diploma project (European Humanities University, Computer Science). This repository is
assessed against the **Microservices** criterion: four business services, one database each, an API
Gateway, and a message broker, all started with one command.

- Architecture, decomposition rationale, diagrams: [`docs/architecture.md`](docs/architecture.md)
- Script of the live walkthrough: [`docs/demo-script.md`](docs/demo-script.md)
- Contracts: [`contracts/`](contracts) (OpenAPI per service, AsyncAPI for events)

## Run it

Prerequisites: Docker with Compose, and a LocalStack auth token. For the verification and demo scripts
also `curl`, `jq`, `python3` and the AWS CLI (it only talks to LocalStack on `localhost:4566`).

```bash
cp .env.example .env          # make up does this too if .env is missing
# put your LocalStack token into .env as LOCALSTACK_AUTH_TOKEN=...
make up                       # builds and starts everything, waits until it is healthy
make verify                   # checks gateway, authorizer, routes, SNS to SQS to DLQ, health
make demo                     # narrated end-to-end scenario through the gateway
make down                     # stop the stack
```

No secret is committed: `.env` is git-ignored and `.env.example` lists variable names and throwaway local
values only. The database passwords in `.env.example` are local-only values.

`make demo` runs every act. To run one: `make demo ACT=auth` (also `purchase`, `diagnosis`,
`escalation`, `resilience`). `DEMO_PAUSE=1 make demo` waits for Enter between steps. The demo creates a
fresh Cognito user on every run, so it can be repeated. Its resilience acts stop and start the project's
own `entitlement` and `expert-consultation` containers and restore them even when a step fails.

### Services

| Service | Port | Responsibility | Own data |
|---|---|---|---|
| `commerce` | `127.0.0.1:8001` | Catalog, orders with mocked payment, first-purchase discount; publishes `order.placed` | `catalog_items`, `catalog_item_labels`, `orders` |
| `entitlement` | `127.0.0.1:8002` | Tier (computed from the last 30 days of paid orders, never stored) and AI and expert quotas with reserve, commit, release | `placed_orders`, quota reservations |
| `plant-care` | `127.0.0.1:8003` | Journal, updates and photos, AI diagnosis behind a model adapter, escalation to an expert, care chat over a knowledge base | 10 tables (journals, updates, diagnoses, escalations, knowledge base, chat) |
| `expert-consultation` | `127.0.0.1:8004` | Expert tickets with a state machine, expert answers, ticket expiry | `tickets`, `answers` |

Each service has its own PostgreSQL 16 container (`commerce-db`, `entitlement-db`, `plant-care-db`,
`expert-db`; not published to the host). The service ports are for health checks and debugging.

The product API is reached **through the gateway** (LocalStack API Gateway, REST API `growpath-gateway`,
stage `v1`), with a Cognito ID token in the `Authorization` header (three read-only routes are public, see below):

```
http://localhost:4566/restapis/<api-id>/v1/_user_request_/v1/<path>
```

Public routes (no token): `GET /items`, `GET /items/{slug}` (commerce) and `GET /plants/{slug}` (plant-care).
Everything else is behind Cognito: `POST /orders` (commerce); `/journals/...`, `/updates/{id}/...` (plant-care);
`GET /me/entitlement` (entitlement); `/expert/tickets/...` (expert-consultation). The entitlement quota API
is internal and has no route. The demo catalog is 15 plant packs (one per PlantSolve plant, `<Species> Starter Pack`)
and two sensors; every item has a `slug`, and `plant_slug` (equal to `slug` for a pack, `null` for a sensor)
links a pack to its plant profile. The plant page calls `GET /items/{slug}` and `GET /plants/{slug}` in parallel and
renders each block on its own, so an order can be placed even when plant-care is down. Test users (LocalStack only): `check@growpath.test` and the expert
`expert@growpath.test`; see `scripts/verify-localstack.sh` and `infra/localstack/init/ready.d/01-provision.sh`.

### Model provider

plant-care calls the vision model through an adapter selected by `PLANT_CARE_MODEL_PROVIDER` in `.env`:

| Value | Use |
|---|---|
| `mock` (default in `.env.example`) | Deterministic answers, no network or key; `PLANT_CARE_MODEL_MOCK_BEHAVIOR` (`complete`, `clarify`, `unavailable`, `malformed`, ...) scripts a specific outcome. Use it for demos and to show failures |
| `openrouter` | Live answers; needs `PLANT_CARE_OPENROUTER_API_KEY` and `PLANT_CARE_OPENROUTER_MODEL` in `.env` (never committed) |
| `bedrock` | Production target (`PLANT_CARE_BEDROCK_MODEL_ID`); the adapter exists, but the local compose stack does not pass the Bedrock settings because this repository never calls real AWS |

The demo prints which provider is active. With a live provider the diagnosis outcome depends on the model
and on network conditions; a failure shows as the `degraded` fallback, which is also what the architecture
promises (see below).

### Care-chat knowledge base

The care chat in plant-care answers from a knowledge base held in the plant-care database
(`kb_documents`, `kb_sections`). Its content is open data from [PlantSolve](https://www.plantsolve.com/):
15 plants (the list is `services/plant-care/app/knowledge_import/plantsolve_plants.txt`) and 5 problem
records, fetched from the static JSON API `https://www.plantsolve.com/api/v1/`. The same import fills the plant
profiles served by the public `GET /v1/plants/{slug}`; the plants file maps the PlantSolve slug, our slug and the
species, and the species must match `plant_species` of the catalog pack. Until `make import-kb` has run, the
endpoint answers `404`.

**Licence and attribution.** PlantSolve `/api/v1/*` is licensed
[CC BY 4.0](https://www.plantsolve.com/terms/) (section 4), which requires visible attribution and a link
to the record. Each source cited in a chat response has a `document_title` ending with `(PlantSolve)` and a
`source_url` pointing at the PlantSolve page of that plant. Problem records have no page of their own on
the site, so their `source_url` is the PlantSolve dataset page (`/dataset/`). The frontend shows "PlantSolve, CC BY 4.0" with a
link to `source_url` for URLs on `www.plantsolve.com`. PlantSolve images and all non-API content (site
design, code, calculators) are not open and are not used. The plant profile response carries `sources`
(`name`, `url`, `license`: PlantSolve, the link to the record, CC BY 4.0), and the frontend shows that link.

**Run it locally.** The import is a one-shot command, separate from `make up`, and needs internet access
from the container:

```bash
make up                          # the stack must be healthy first (migrations done)
make import-kb                   # fetch, convert and store
make import-kb ARGS=--dry-run    # show what would change, write nothing
```

It runs `python -m app.knowledge_import` in a `kb-import` container (compose profile `import`) built from
the plant-care image, against the plant-care database only. It is idempotent: a re-run changes nothing
unless the source changed, and documents that are no longer in the list are reported, never deleted.

An empty knowledge base is a normal state, not an error: the chat answers that the topic is not in the
knowledge base, and the question still spends one AI quota unit. plant-care never waits for the import.

**Running it in Kubernetes.** Use the same image and command as a Job: once after install (for example a
Helm post-install hook or an Argo CD PostSync hook), and as a CronJob for refreshes (for example weekly).
The service never waits for either, and an empty knowledge base does not fail readiness. No Kubernetes
manifests are in this repository (EKS is out of scope here); this paragraph describes how the same
container would be run.

### Catalog images

Item pictures live in a separate public-read S3 bucket, `growpath-media` (the policy allows reading
objects only, never listing the bucket). The file name is the item `slug`: `<base>/<slug>.webp`, one
format (WebP, at most 1200 px on the long side). An item without a picture answers 404 and the
client shows a placeholder; the catalog API does not carry an image field.

[`media/catalog-images.csv`](media/catalog-images.csv) is the only list of images, with the columns
`slug, url, author, license, page`. `url` is an https link to download, or a file under
`media/source/` (stock sites that do not allow downloads by link; those originals are git-ignored).
`page` is the source page of the photo. Only licences that allow commercial use are accepted: CC0,
public domain, CC BY, CC BY-SA and the free licences of Pixabay, Pexels, Unsplash and Magnific. The
upload writes a `credits.json` next to the images with the author, licence and source page of every
image; the client shows them.

```bash
make catalog-images             # uploads to LocalStack (needs uv; creates nothing outside the bucket)
make client-env                 # prints VITE_MEDIA_BASE_URL=http://localhost:4566/growpath-media
```

The script is idempotent: an image is uploaded again only when its source or the processing changed.
Against real S3, run it without `AWS_ENDPOINT_URL` and with the normal AWS credential chain
(`AWS_PROFILE` or a role; keys are never stored here), choosing the bucket with `--bucket` or
`MEDIA_BUCKET`: `uv run --script scripts/upload-catalog-images.py --bucket my-media-bucket`. The
public URL there is `https://<bucket>.s3.<region>.amazonaws.com/<slug>.webp` (or the CloudFront
domain in front of the bucket). On real S3 the bucket needs the same read-objects-only policy, and
"Block public access" turned off for it.

### Tests

```bash
make sync          # create .venv with all services and dev tools (uses uv)
make lint          # ruff check and format check
make test-unit     # unit and contract tests, no database, coverage gate 70% per service
make test-db-up    # throwaway PostgreSQL 16 for the DB tests (compose.test.yml)
make test          # everything including DB tests (starts the test database itself)
make test-db-down
make contracts     # regenerate contracts/openapi/*.yaml from the apps (commit the diff)
```

## Where each requirement is implemented

| Requirement | Status | Where | See it live |
|---|---|---|---|
| At least 3 business services with separate domains | Done (4) | [`services/`](services); rationale in [`docs/architecture.md`](docs/architecture.md#3-decomposition-rationale) | `docker compose ps` |
| Own database per service | Done | One Postgres container per service in [`docker-compose.yml`](docker-compose.yml); Alembic migrations in `services/*/migrations`; no cross-service access | `docker compose ps` shows four `*-db` containers |
| Architecture diagram, decomposition rationale, deployment diagram | Done | [`docs/architecture.md`](docs/architecture.md) (context, deployment, sequence diagrams) | |
| Service contracts | Done | [`contracts/openapi/`](contracts/openapi) (4 files), [`contracts/asyncapi/growpath-events.yaml`](contracts/asyncapi/growpath-events.yaml); contract tests `services/*/tests/unit/test_contracts.py`, `test_event_contracts.py` | `make test-unit` |
| Health endpoints | Done | `/health/live` and `/health/ready` in every service (`app/api/health.py`); ready checks the database and the consumed SQS queues | `make verify` (section 9), demo act 0 |
| Logs with correlation ID | Done | JSON logs from [`libs/observability`](libs/observability); gateway `$context.requestId` becomes `X-Correlation-Id`, forwarded in HTTP calls and SNS attributes | Demo act 4 prints one id across three services; or `docker compose logs` and `jq` ([how](docs/architecture.md#9-observability)) |
| API Gateway: routing | Done | [`infra/localstack/init/ready.d/01-provision.sh`](infra/localstack/init/ready.d/01-provision.sh) | `make verify` (section 4) |
| API Gateway: authorization | Done | Cognito JWT authorizer on every route except the public read-only ones (`GET /items`, `GET /items/{slug}`, `GET /plants/{slug}`); identity headers set from claims, overwriting client values; experts group check in expert-consultation | `make verify` (sections 3 and 5), demo act 1: `401` without a token (the catalog is `200`), forged `X-User-Groups` gets `403` |
| API Gateway: rate limit | Declared, not demonstrable locally | Stage throttle (2 rps, burst 2) in `01-provision.sh`; LocalStack does not enforce it, and there is no service-side `429` yet | Demo act 1 shows the declared setting and that a burst still returns `200`; see Known limitations |
| Message broker | Done | SNS topic at the publisher, SQS queue per subscriber, five events: `order.placed`, `escalation.requested`, `ticket.created`, `ticket.answered`, `ticket.cancelled` ([table](docs/architecture.md#5-asynchronous-interaction)) | Demo acts 2, 4, 5b |
| Dead-letter queues, idempotent consumers, at-least-once publishing | Done | `maxReceiveCount = 2` DLQ per queue; idempotent handlers; published-at flag plus repeater in commerce, plant-care and expert-consultation instead of an outbox table | `make verify` (section 7), demo acts 5b and 5c |
| Resilience: timeouts, retries with jitter | Done | [`services/plant-care/app/resilience.py`](services/plant-care/app/resilience.py), [`quota.py`](services/plant-care/app/quota.py), settings in `config.py` | |
| Resilience: circuit breaker, bulkhead, fallback | Done | `CircuitBreaker` (entitlement and model), [`llm/guard.py`](services/plant-care/app/llm/guard.py) bulkhead, `degraded` and `diagnosis_pending` fallbacks, `503` with `Retry-After` | Demo act 5a: entitlement stopped, breaker opens, `503` with `Retry-After`, recovery |
| Distributed tracing | Consciously skipped | Correlation IDs in structured logs instead; see [architecture, observability](docs/architecture.md#9-observability) | |

## Known limitations

### Rate limiting at the API Gateway

The gateway is configured with a stage-level throttle (`rateLimit=2`, `burstLimit=2`, see
`infra/localstack/init/ready.d/01-provision.sh`). LocalStack stores this setting but does not enforce
it, so no `429` is produced locally. The LocalStack documentation states this explicitly: stage and
method throttling "have no effect", and usage plans validate API keys but "do not enforce throttling
or quota limits". We confirmed it by experiment: 30 rapid authorized requests all returned `200`, both
with the stage throttle and with a usage plan plus API key (which did enforce the key, `403` without it).

What the gateway does enforce locally: routing, Cognito JWT authorization (`401` without or with an
invalid token) and `X-Correlation-Id` injection from `$context.requestId`.

On real AWS API Gateway the same configuration returns `429`. Rate limiting is therefore declared
at the gateway and is **not demonstrable locally**. The service-side fallback (a per-user rate limit
middleware returning `429` as RFC 7807) is **not implemented**; it will be added only if a demonstrable
limit is required. Nothing in this repository claims a working `429`.

### Identity headers and the `users` group

The gateway maps `cognito:groups` to `X-User-Groups`, but a mapped header replaces the client's value
only when the claim has a value. A token without the claim would let a forged `X-User-Groups` through.
Every user must therefore be in the Cognito group `users` so the claim is never empty. The provisioning
script and the demo do this; a real deployment needs the same rule at sign-up (or an authorizer that
always sets the header).

### Other notes

- **LocalStack:** the API Gateway invoke URL is the LocalStack form shown above, not an AWS URL. The
  state of LocalStack is not persisted: after a LocalStack restart the init script runs again. The
  ids a client needs are fixed (LocalStack `_custom_id_`): API `growpath` (so the invoke URL is
  `http://localhost:4566/restapis/growpath/v1/_user_request_/v1/<path>`), user pool
  `us-east-1_growpath`, app client `growpathclient`, CloudFront `growpathspa`
  (`http://growpathspa.cloudfront.localhost.localstack.cloud:4566/`). The stored data is not:
  uploaded images and the frontend build must be uploaded again (`make catalog-images`,
  `make deploy-frontend`), and the Cognito users are created again by the init script.
- **Expiry is not live-demonstrable:** a ticket expires 72 hours after creation (an author's assumption);
  `ticket.cancelled` and the quota release are covered by tests (`services/expert-consultation/tests`).
- **Care chat** over the knowledge base exists in plant-care (`/journals/{id}/chat/turns`) but is outside the
  microservices walkthrough.
- **Tracing is skipped** on purpose; correlation IDs in structured logs are the supported way to follow a request.
- **Scope:** only LocalStack is used; no real AWS account is touched.

## Development

Python dependencies are managed with [uv](https://docs.astral.sh/uv/) as one workspace: a single
`uv.lock` pins every package for all services, and Docker images are built from it with
`uv sync --frozen`. Layers in every service: `api/` to `service/` to `repository/` to `models/`.
