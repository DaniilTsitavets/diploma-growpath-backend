# Demo script for the expert call (about 30 minutes)

Goal: show, on a running system, that the **Microservices** criterion is met: four business services with
their own databases, an API Gateway (routing, authorization, rate limit), a message broker with dead-letter
queues, resilience, health endpoints and logs with a correlation ID. Everything is driven by
`scripts/demo.sh`; the commands below are what to type and what to point at.

## Before the call (15 minutes ahead)

1. `make up` and wait for it to return (all containers healthy). Then `make verify` and keep its output.
2. Set `PLANT_CARE_MODEL_PROVIDER=mock` in `.env` for a deterministic run, or keep `openrouter` if you want a
   live model (its answer and latency vary). After changing `.env`, run `make up` again.
3. Rehearse once: `make demo`. It creates a new Cognito user each time, so it is safe to repeat.
4. Open four things: a terminal in the repository root (font large), a second terminal with
   `docker compose --env-file .env logs -f` ready, a browser tab with `docs/architecture.md` rendered on
   GitHub, and the `contracts/` folder in the editor.
5. Record a backup video of a full `make demo` run (see Fallbacks).

For stepping through, use `DEMO_PAUSE=1 make demo` (Enter between steps) or run act by act
(`make demo ACT=auth`, `purchase`, `diagnosis`, `escalation`, `resilience`). Acts are independent: each one
sets up what it needs, so you can skip or repeat any of them.

## Agenda

| Time | Block | What to do |
|---|---|---|
| 0:00 - 3:00 | Context and architecture | Show the diagrams in `docs/architecture.md` (context, deployment) |
| 3:00 - 5:00 | The running stack | `docker compose --env-file .env ps`, `make verify` output, repository layout |
| 5:00 - 9:00 | Act 1: gateway and identity | `make demo ACT=auth` |
| 9:00 - 13:00 | Act 2: purchase and events | `make demo ACT=purchase` |
| 13:00 - 17:00 | Act 3: diagnosis | `make demo ACT=diagnosis` |
| 17:00 - 22:00 | Act 4: escalation and correlation ID | `make demo ACT=escalation` |
| 22:00 - 28:00 | Act 5: resilience | `make demo ACT=resilience` |
| 28:00 - 30:00 | Contracts, tests, wrap-up | Open `contracts/`, run `make test-unit` summary line |

A full `make demo` takes about 2 to 3 minutes without pauses (measured at 1 min 58 s with a live provider, where
each diagnosis took 15 to 20 seconds). The timings above leave room to talk between acts.

## 0:00 - Context and architecture (3 min)

Say: GrowPath is a hobby plant-growing platform. Four services, split by business capability and data
ownership: `commerce`, `entitlement`, `plant-care`, `expert-consultation`. One Postgres per service,
SNS and SQS between them, one API Gateway in front, Cognito for identity.

Show on screen, in `docs/architecture.md`:

- Section 1, the context diagram: who talks to whom.
- Section 2, the deployment diagram: docker-compose plus LocalStack standing in for AWS.
- Section 3, why Commerce and Entitlement are separate (different reasons to change, different
  consistency model, and checkout must not sit on the diagnosis path).

The expert should take away: no shared database, no shared business library, no 2PC; the sync chain is two
services long; the rest is events.

## 3:00 - The running stack (2 min)

```bash
docker compose --env-file .env ps
```

Point at: four service containers, four `*-db` containers (one database each), one `localstack`. Ports are
bound to `127.0.0.1`. Then show the saved `make verify` output (or run it, about 25 seconds): authorizer,
routes, SNS to SQS to DLQ, health of all four services.

```bash
for p in 8001 8002 8003 8004; do curl -s localhost:$p/health/ready; echo; done
```

Point at: each service has `/health/live` and `/health/ready`; ready checks its database and the queues it
consumes.

## 5:00 - Act 1: gateway, authorization, rate limit (4 min)

```bash
make demo ACT=auth
```

What the expert should see:

1. `no token: 401` and `garbage token: 401`. The Cognito authorizer rejects before any service is reached.
2. A new user and the expert are created and log in. The printed claims show `sub` and
   `cognito:groups: ["users"]`; the expert has `["users","experts"]`. Tokens are printed truncated.
3. A user who sends `X-User-Groups: experts` still gets `403`, and a user who sends a forged `X-User-Id`
   still gets their own entitlement (the `sub` of the token). Explain: the gateway maps claims to headers
   and overwrites the client's values (`IDENTITY_HEADERS` in `infra/localstack/init/ready.d/01-provision.sh`).
4. The internal quota API has no route: `403`.
5. Rate limit: the declared stage throttle (`throttlingRateLimit: 2`) is shown, and a burst of 15 requests
   returns `200` for all. **Say this clearly:** LocalStack stores the limit but does not enforce it, so a
   `429` cannot be shown locally; on real API Gateway the same stage settings return `429`. There is no
   service-side `429` yet. See Known limitations in the README.

Mention the `users` group caveat (README, Known limitations) if asked about spoofing.

## 9:00 - Act 2: purchase and events (4 min)

```bash
make demo ACT=purchase
```

What to point at:

- First purchase: list price 25.00, discount 5.00, paid 20.00. The same `Idempotency-Key` returns the same
  order with `200`.
- "waiting for order.placed to reach plant-care": commerce published `order.placed` to SNS, the plant-care
  queue delivered it, plant-care created the journal draft. Eventual consistency in action; it is usually under a second.
- `tier=starter ... expert_quota_30_days=0/0`, then a second purchase and `tier=hobbyist ... 0/2`. Explain
  that the tier is a function of the 30-day paid total computed from entitlement's own projection of
  orders, never stored, and that the Starter tier has no expert quota, which is why the script buys the most expensive pack.

Code to show if asked: `services/commerce/app/service/purchase.py` (idempotency key, publish after commit,
republisher), `services/entitlement/app/service/tiers.py`.

## 13:00 - Act 3: journal, photos, diagnosis (4 min)

```bash
make demo ACT=diagnosis
```

What to point at:

- Photos go straight to S3 by a presigned URL issued by plant-care (the URL is printed without its
  signature); the database stores only object keys.
- `tier=starter ai_quota_today=1/3` before the diagnosis: creating the update already reserved one AI unit.
- The diagnosis call: `status`, `model_effort`, candidates, `has_dominant_candidate`, `escalation_offered`
  with the mock provider. After it, the quota is `1/3` (committed) with a result, or `0/3` when the model
  failed and the diagnosis became `degraded` (reservation released, nothing charged).
- This is the resilience story of plant-care in one picture: reserve in entitlement, call the model behind a
  bulkhead, circuit breaker and retry, then commit or release.

If you use the mock provider and want to show the model-failure path deliberately:

```bash
PLANT_CARE_MODEL_MOCK_BEHAVIOR=unavailable make up      # recreates plant-care only
make demo ACT=diagnosis                                  # status=degraded, quota released
PLANT_CARE_MODEL_MOCK_BEHAVIOR=complete make up          # back to normal
```

## 17:00 - Act 4: escalation across three services (5 min)

```bash
make demo ACT=escalation
```

What to point at, in order:

1. `POST .../escalation` returns `201 status=requested`. plant-care reserved the expert quota in
   entitlement, then published `escalation.requested` with a snapshot of the context.
2. "waiting for the ticket in the expert queue": the expert (a different Cognito identity in the
   `experts` group) sees a ticket whose context carries the species, the question and the photos.
3. **The correlation-ID block.** The script prints the log lines of one gateway request id from the whole stack:
   entitlement reserved the quota, plant-care sent the event, expert-consultation created the ticket and
   published `ticket.created`, entitlement and plant-care applied it. `services that logged this id:
   entitlement, expert-consultation, plant-care`; commerce is absent because this flow does not touch it. The same view by hand:

   ```bash
   docker compose --env-file .env logs --no-color --no-log-prefix --since 10m \
     | jq -Rr --arg id "<correlation-id>" 'fromjson? | select(.correlation_id == $id) | [.timestamp, .service, .message] | @tsv'
   ```

4. The expert answers; `ticket.answered` flows back; the user's escalation turns `answered` and shows the text.
   A second correlation ID (the expert's request) links expert-consultation and plant-care.
5. entitlement committed the expert reservation on `ticket.created` (`expert_quota_30_days=1/2`).

Say: three asynchronous hops, one correlation ID, no synchronous call between plant-care and
expert-consultation.

## 22:00 - Act 5: resilience (6 min)

```bash
make demo ACT=resilience
```

**5a. entitlement down.** The script stops the `entitlement` container.

- Submitting an update still succeeds and the diagnosis is `diagnosis_pending` (fallback).
- Running the diagnosis returns `503` with `Retry-After: 5` and an RFC 7807 body. Six attempts in a row: the
  first ones retry with jitter, then the circuit breaker opens (`circuit breaker opened`, then
  `entitlement call rejected by the open breaker`) and later calls fail in a few milliseconds.
- The user's data is intact (`GET .../updates` is `200`). The container is restarted, the breaker allows
  one probe after its reset timeout, and the diagnosis works again.

**5b. expert-consultation down.** The script stops the container.

- The escalation is still accepted (`requested`) because the broker decouples the services; SQS holds the
  message (`1 message(s)`).
- plant-care's repeater republishes the unconfirmed escalation after its grace period; the queue shows
  `2 message(s)`: publishing is at-least-once.
- After the restart exactly one ticket appears; the log shows the duplicate ignored. This is the idempotent consumer.

**5c. Dead-letter queue.** A message without `event_type` is sent to `plant-care-order-placed`. plant-care
logs two failed attempts and SQS moves the message to `plant-care-order-placed-dlq` (`maxReceiveCount = 2`).
The script removes its own message from the DLQ afterwards.

Safety: the script restores the stopped containers on exit, also after an error or Ctrl-C. If the terminal is
killed hard, run `docker compose --env-file .env start entitlement expert-consultation`.

## 28:00 - Contracts, tests, wrap-up (2 min)

- Open `contracts/openapi/` (one file per service, `x-gateway: public|internal`) and
  `contracts/asyncapi/growpath-events.yaml` (five events, topics, queues, DLQs).
- `make test-unit`: unit and contract tests with a 70% coverage gate per service. The
  contract tests fail when code and OpenAPI or AsyncAPI drift apart.
- Mention honestly what is not there: no tracing (correlation IDs instead), the rate limit is declared but
  not enforceable on LocalStack, expiry of tickets is covered by tests and not shown live (72-hour TTL).

## Fallbacks

| Problem | What to do |
|---|---|
| Anything breaks and cannot be fixed in two minutes | Switch to the recorded video of `make demo`; keep narrating with this script |
| A service is not healthy | `docker compose --env-file .env ps`, `docker compose --env-file .env logs <service> --tail 50`, then `make up` (it recreates what changed) |
| LocalStack was restarted (new gateway id, empty Cognito state) | Re-run the act; the script looks the gateway up each run. If a step fails with "does not correspond to a deployed API", just run it again |
| A waiting step times out ("waiting for ... timed out") | The message names the queue and its DLQ. Inspect: `aws sqs get-queue-attributes --queue-url <url> --attribute-names All` with `AWS_ENDPOINT_URL=http://localhost:4566` (dummy credentials `test`/`test`) |
| The live model is slow, fails or returns `degraded` | That is the designed fallback and worth showing. For a deterministic run set `PLANT_CARE_MODEL_PROVIDER=mock` in `.env` and `make up` |
| A resilience act was interrupted | `docker compose --env-file .env start entitlement expert-consultation` |
| The expert asks for a repeat of one flow | Every act is independent and repeatable: `make demo ACT=<name>` |

## Q&A cheat sheet

**Why four services and not three or one?** Split by capability and data ownership; see
`docs/architecture.md` section 3. Commerce and Entitlement differ in rate of change, consistency model and
availability needs (checkout must not be on the diagnosis path). A notification service was left out because
it is not business logic here.

**How do you keep data consistent without distributed transactions?** Each service commits locally; cross-service
facts travel as events (at-least-once) and are applied idempotently. Quotas use reserve, commit, release with expiring
reservations, so a crash never leaks quota. Code: `services/entitlement/app/service/quota.py`.

**Is delivery exactly-once?** No: at-least-once publishing (a published-at flag plus a repeater instead of an outbox
table) and idempotent consumers (`ON CONFLICT DO NOTHING`, guarded updates). Code: `services/commerce/app/service/republisher.py`,
`services/plant-care/app/service/escalation_repeater.py`, `services/expert-consultation/app/service/maintenance.py`.

**What happens when the broker or a subscriber is down?** SQS buffers; unpublished rows are republished; failed messages
go to the DLQ after `maxReceiveCount`. Shown in acts 5b and 5c.

**What happens when entitlement is down?** `503` + `Retry-After`, circuit breaker, updates stored as `diagnosis_pending`.
Code: `services/plant-care/app/quota.py`, `services/plant-care/app/resilience.py`.

**What happens when the AI model fails?** Bulkhead, retry with jitter, breaker; then the diagnosis is `degraded`
with a fixed recommendation and the AI quota is released. Code: `services/plant-care/app/llm/guard.py`,
`services/plant-care/app/service/diagnosis.py`.

**Can a user forge their identity?** No: the gateway overwrites `X-User-Id` and `X-User-Groups` from verified claims, and
entitlement's user id comes from the token. Caveat: every user must be in the `users` group. Shown in act 1.

**Where is rate limiting?** Declared on the gateway stage, not enforced by LocalStack, no service-side `429` yet. Say it
plainly; see README, Known limitations.

**Where is tracing?** Deliberately skipped; structured JSON logs with a correlation ID that crosses HTTP and SNS cover request
following. See `docs/architecture.md` section 9.

**How are the contracts verified?** Generated OpenAPI per service is compared with the committed file by tests; real responses are
validated against it; event payloads are validated against the AsyncAPI JSON Schemas. `make test-unit`.

**How would this scale?** Services are stateless; queue consumers compete on SQS; repeaters claim rows with
`FOR UPDATE SKIP LOCKED`, so several replicas do not double-process. Databases are separate per service.

**How are databases and schemas managed?** One Alembic migration chain per service, applied at container start
(`services/*/migrations`); the schemas are checked against PostgreSQL 16 in the DB tests.

**Why SNS and SQS?** They are the AWS-native pub/sub with fan-out, per-subscriber queues, redrive to a DLQ and filter policies,
and they run on LocalStack.

**How long is the synchronous chain?** Gateway, plant-care, entitlement: two services. Entitlement calls nobody.

**Why does the Starter tier have no expert quota?** It is a product rule (configurable in `services/entitlement/app/config.py`);
that is why the demo buys a second pack.
