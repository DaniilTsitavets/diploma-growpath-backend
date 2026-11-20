# GrowPath: Microservices demo plan

About 30 minutes. Every step is run on the live system; the requirement it proves is named next to it.

| # | Block | Time | What we show | Requirement it proves |
|---|---|---|---|---|
| 0 | Architecture | 3 min | Context and deployment diagrams, why four services | 3+ business services, decomposition rationale, deployment scheme |
| 1 | Running stack | 2 min | `docker compose ps`, `make verify`, `/health/ready` of every service | One database per service, health endpoints |
| 2 | API Gateway | 4 min | No token gives `401`, forged identity headers are overwritten, internal API is not routed | Gateway: routing, authorization |
| 3 | Purchase and events | 4 min | Order in `commerce`, `order.placed` reaches `entitlement` and `plant-care` through SNS and SQS | Message broker, asynchronous communication |
| 4 | AI diagnosis | 4 min | Quota reserve, model call, commit or release | Sync call between services, quota consistency without 2PC |
| 5 | Expert escalation | 5 min | Three services, three asynchronous hops, one correlation ID in the logs | Broker, logs with correlation ID |
| 6 | Resilience | 6 min | `entitlement` down: `503` with `Retry-After`, circuit breaker, recovery. `expert-consultation` down: no loss, at-least-once, idempotent consumer. Poison message goes to the DLQ | Fault tolerance: timeouts, retries, circuit breaker, bulkhead, fallback, DLQ |
| 7 | Contracts and tests | 2 min | `contracts/` (OpenAPI, AsyncAPI), `make test-unit` | Service contracts, contract tests |

Then questions.

## Stated openly

- Rate limit is declared on the gateway stage; LocalStack does not enforce it, so no `429` can be shown locally.
- Distributed tracing is not implemented; structured logs with a correlation ID follow a request across services.
- Ticket expiry (72 hours) is covered by tests, not shown live.

## Commands

```bash
make up && make verify            # before the call
make demo                         # everything, about 2 to 3 minutes
make demo ACT=auth                # also: purchase, diagnosis, escalation, resilience
DEMO_PAUSE=1 make demo            # pause for Enter between steps
```

Detailed script and answers to likely questions: [`demo-script.md`](demo-script.md).
