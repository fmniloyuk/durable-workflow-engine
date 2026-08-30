# Failure Modes

The engine is designed by enumerating ambiguous failure windows rather than assuming a happy-path queue consumer.

| Failure | Detection | Recovery | Residual risk |
|---|---|---|---|
| API dies before DB commit | client sees error/timeout | retry same `Idempotency-Key` | none if client retries |
| API dies after DB commit before response | duplicate client submission | unique workflow idempotency key returns existing workflow | client must reuse key |
| Scheduler dies before outbox publish | unpublished DB row | next scheduler pass publishes | publication delayed |
| Scheduler dies after XADD before marking outbox published | row still unpublished | message published again | duplicate message expected |
| Redis loses/restarts | queued/scheduled state remains in PostgreSQL | old queued tasks are republished; outbox retries | temporary delay; AOF config reduces loss |
| Worker dies before DB lease | task remains `queued` | orphan republisher emits another queue hint | duplicate queue entries |
| Worker dies while leased | lease timestamp expires | reaper schedules retry | handler may have partially executed |
| Worker dies after external side effect before result commit | lease expires | task retries | external target must use idempotency key |
| Worker dies after result commit before Redis ACK | terminal DB state exists | duplicate message fails lease acquisition and is ACKed | extra queue traffic only |
| Heartbeat delayed beyond lease TTL | scheduler sees expired lease | retry on another worker | overlapping execution; external idempotency required |
| Handler times out | asyncio timeout | ordinary retry/backoff policy | blocking native code may ignore cancellation |
| Repeated task failure | attempt counter | task enters durable dead-letter state | operator/manual remediation needed |
| Malformed queue message | required fields missing | copy payload/error to poison DLQ and ACK source | malformed producer bug remains |
| Dependency task dead-letters | workflow terminal failure | downstream tasks remain non-runnable | explicit compensation not implemented |
| Cancellation while queued | durable workflow state | queued/pending/scheduled tasks marked cancelled | stale queue entries harmless |
| Cancellation while running | worker checks workflow before success commit | durable success rejected/converted to cancelled | already executed remote side effect cannot be undone |
| Redis concurrency permit leaked | permit has expiry score | subsequent acquire removes expired entries | temporary under-utilization |
| Multiple schedulers race | row locks + `SKIP LOCKED` | one scheduler owns each selected row | duplicate publish still possible after crash window |
| Clock skew | DB/application timestamps differ | local Docker assumes synchronized clocks | production should use NTP and preferably DB time for lease comparisons |

## Poison task handling

A **poison message** is structurally invalid transport input (for example, missing `task_id`). The consumer moves its representation to `dwe:dlq:poison` and ACKs it so it cannot wedge the group forever.

A **dead-letter task** is a valid durable task that exhausted its retry budget. Its authoritative state is `tasks.state = dead_letter`; an outbox event also mirrors it to `dwe:dlq:tasks` for operational tooling.

## Redis consumer PELs

The demo does not depend on claiming another consumer's Pending Entries List for correctness. A consumer can die before lease acquisition, leaving an entry stranded. The scheduler detects sufficiently old durable `queued` tasks and republishes a new queue hint. This intentionally trades extra messages for a simpler source-of-truth model.

A production evolution could add `XAUTOCLAIM` to reduce queue churn; it would be an optimization, not the durability authority.

## Chaos verification

`scripts/chaos-compose.sh` drives the complete Compose stack and verifies:

1. worker kill during a long task;
2. an explicitly duplicated Redis Stream message;
3. worker pause long enough to miss heartbeat/lease renewal;
4. Redis restart while a workflow is active;
5. API restart while a workflow is active;
6. deterministic transient handler failure followed by retry;
7. duplicate client submission with the same idempotency key.

The script fails unless each workflow reaches the expected terminal state.
