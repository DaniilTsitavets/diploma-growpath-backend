# diploma-growpath-backend

Backend of **GrowPath**, a guided hobby plant-growing platform: a physical starter pack, a
multimodal AI assistant for diagnosing plant problems, and a quota-based escalation path to a live
botanist expert. Bachelor diploma project (European Humanities University, Computer Science).

> Status: work in progress. This README is filled in as the implementation lands.

## Services

| Service | Responsibility |
|---|---|
| `commerce` | Catalog, orders with mocked payment, first-purchase discount; publishes `order.placed` |
| `entitlement` | Account tier (computed from the last 30 days of orders) and AI / expert quotas with reserve, commit and release |
| `plant-care` | Plant journal, updates, AI diagnosis behind a model adapter, checkpoints |
| `expert-consultation` | Expert tickets with a state machine, expert role, context snapshot |

Each service owns its own Postgres database. Services talk synchronously over REST (`/v1`) and
asynchronously through SNS topics and SQS queues with dead-letter queues. A single AWS API Gateway
(emulated locally with LocalStack) is the entry point: routing, Cognito JWT authorization and rate
limiting.

## Running locally

Requirements: Docker with Compose, a LocalStack auth token exported as `LOCALSTACK_AUTH_TOKEN`
(never committed), and a copy of `.env.example` saved as `.env`.

The exact commands will be added once the compose stack exists.

## Where each requirement is implemented

| Requirement | Where | Status |
|---|---|---|
| At least 3 business services, separate domains | `services/` | planned |
| Own database per service | `docker-compose.yml`, `services/*/` | planned |
| Architecture diagram, decomposition rationale, deployment diagram | `docs/architecture.md` | planned |
| Service contracts (OpenAPI / AsyncAPI) | `contracts/` | planned |
| Health endpoints | `/health/live`, `/health/ready` in each service | planned |
| Logs with correlation ID | JSON logs, `X-Correlation-Id` set at the gateway | planned |
| API Gateway: routing, authorization, rate limiting | `infra/localstack/` | routing, authorization, correlation ID verified; rate limit declared, not enforced by LocalStack (see Known limitations) |
| Message broker (pub/sub, DLQ, idempotent consumers) | SNS + SQS, `infra/localstack/` | planned |
| Resilience (timeouts, retries, circuit breaker, fallback) | `services/plant-care/` | planned |

## Known limitations of the local environment

### Rate limiting at the API Gateway

The gateway is configured with a stage-level throttle (`rateLimit=2`, `burstLimit=2`, see
`infra/localstack/init/ready.d/01-provision.sh`). LocalStack stores this setting but does not
enforce it, so no `429` is produced locally. The LocalStack documentation states this explicitly:
stage and method throttling "have no effect", and usage plans validate API keys but "do not enforce
throttling or quota limits". We confirmed it by experiment: 30 rapid authorized requests all returned
`200`, both with the stage throttle and with a usage plan plus API key (which did enforce the key, `403`
without it).

What the gateway does enforce locally: routing, Cognito JWT authorization (`401` without or with an
invalid token) and `X-Correlation-Id` injection from `$context.requestId`.

On real AWS API Gateway the same configuration returns `429`. Rate limiting is therefore declared
at the gateway and is not demonstrable locally. Status: the service-side fallback (a per-user rate
limit middleware returning `429` as RFC 7807) is **not implemented yet**; it will be added only if a
demonstrable limit is required.

## Development

Python dependencies are managed with [uv](https://docs.astral.sh/uv/) as one workspace: a single
`uv.lock` pins every package for all services, and Docker images are built from it with
`uv sync --frozen`.

```
make sync   # create .venv with all services and dev tools
make lint   # ruff check and format check
make test   # unit tests with coverage (each service must stay at 70% or above)
make lock   # refresh uv.lock after changing dependencies
```
