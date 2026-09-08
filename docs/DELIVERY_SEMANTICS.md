# Delivery Semantics

## At-most-once

An at-most-once system can ACK/remove a message before executing it and decline to retry if the worker crashes. The benefit is that the queue itself will not cause duplicate execution. The cost is lost work whenever failure occurs after ACK but before completion.

This engine does **not** choose at-most-once for task execution because durable workflows are expected to make progress after worker failure.

## At-least-once

This engine uses at-least-once delivery and recovery:

- Redis Stream entries can be duplicated.
- The outbox may republish after an ambiguous publish/DB-commit outcome.
- Lease expiry can retry a task when the previous worker's status is uncertain.
- Old queued tasks can be republished if a queue entry was lost or stranded in a consumer PEL.

At-least-once delivery means a handler can run more than once. PostgreSQL conditional lease acquisition removes many duplicates, but it cannot eliminate the classic crash window around an external side effect.

## Why “exactly once” is not a general guarantee

Consider a payment call:

1. worker acquires task lease;
2. worker calls payment provider;
3. provider charges the card and returns success;
4. worker process/host dies before it commits task success;
5. lease expires and another worker retries.

The second worker cannot know from PostgreSQL whether step 3 happened. If the payment provider and PostgreSQL do not participate in one atomic transaction, there is no general mechanism that can both avoid duplicate payment and guarantee retry after uncertainty.

The opposite ordering is also unsafe: marking the task succeeded before charging can lose the payment if the worker dies between those operations.

## Exactly-once effects with idempotency

The practical solution is to make effects idempotent. For the payment example, derive a stable key such as:

```text
workflow submission key + task key
invoice-2026-0001:charge_customer
```

Send that key to a provider that guarantees repeated requests with the same idempotency key resolve to one effect. Both attempt 1 and attempt 2 may execute the HTTP request, but the externally visible charge occurs once.

`examples/idempotency_demo.py` demonstrates the difference between a naive effect and a target that deduplicates by idempotency key.

### Important limitation of a local idempotency table

Writing `effect_records` in PostgreSQL before/after a remote API call is **not by itself sufficient** for exactly-once remote effects:

- record first, crash before remote call -> future retry may incorrectly suppress an effect that never happened;
- remote call first, crash before record -> retry may duplicate the effect.

A local idempotency table is sufficient only when the side effect is in the same transactional database, or when combined with a remote protocol that itself accepts/reconciles an idempotency key.

## Transactional outbox

When a database state transition should cause an external publication, the API/worker writes an `outbox_events` row in the **same PostgreSQL transaction** as the state change. The scheduler later publishes that row to Redis and marks it published.

Two relevant crash windows become safe:

- crash before Redis publish: row stays unpublished and is retried;
- crash after Redis publish but before `published_at` commit: row is published again.

The second case creates a duplicate, which is why consumers must be idempotent. The outbox converts “possible lost publication” into “possible duplicate publication,” matching the engine's at-least-once design.

## Redis ACK ordering

A worker ACKs a stream entry only after its result/failure transition is durably committed. If it crashes before ACK, Redis may retain/redeliver the entry; if it crashes after commit but before ACK, the duplicate cannot reacquire a task that is already terminal.

## Lease fencing

A worker may commit task success only while its `lease_owner` still matches. After lease expiry/reassignment, the old worker's durable commit is rejected. This fences database state, but it cannot rewind an already completed external side effect; external idempotency/fencing is still required.

## Summary

| Property | Engine behavior |
|---|---|
| Queue delivery | At least once |
| Task execution | At least once under failure uncertainty |
| Durable state transition | Fenced by conditional PostgreSQL lease/state checks |
| Workflow submission | Deduplicated by client idempotency key |
| Queue publication | Transactional outbox, at least once |
| Arbitrary external effect | Not exactly once by default |
| External effect with provider idempotency | Exactly-once *effect* can be achieved |
