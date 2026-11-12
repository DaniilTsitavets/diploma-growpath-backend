# Contracts

The contracts are the source of truth that the code is checked against.

- `openapi/<service>.yaml`: the REST API of each service (OpenAPI 3.1). **Generated** from the
  running FastAPI app. Every operation carries `x-gateway: public|internal`; internal ones
  (health checks, entitlement quotas) are also tagged `internal` and are not routed by the gateway.
  Errors are `application/problem+json` (RFC 7807), declared as the `default` response.
- `asyncapi/growpath-events.yaml`: the events between services (AsyncAPI 3.0). **Written by hand**:
  SNS topics, per-subscriber SQS queues with DLQs, message attributes and JSON Schema payloads.

## How they are checked

Each service has contract tests in `services/<service>/tests/unit/`, run with the normal unit suite
and needing no database or network:

- `test_contracts.py`: the committed OpenAPI file equals what the app generates, every route is
  documented and the other way round, and real responses validate against the documented schemas.
- `test_event_contracts.py`: the payloads and attributes the publishers really produce validate
  against the AsyncAPI schemas, and each consumer's codec accepts the contract examples and rejects
  payloads that break the contract.

## Changing a contract

- REST: change the code, then run `make contracts` to rewrite the OpenAPI files (it sets
  `UPDATE_CONTRACTS=1` for the OpenAPI tests) and commit the diff. Never edit these files by hand.
- Events: edit the AsyncAPI file first, then change the publisher and consumers until the tests pass.
