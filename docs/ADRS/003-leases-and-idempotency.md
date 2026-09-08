# ADR 003: Leases for liveness, idempotency for effects

- Status: Accepted
- Date: 2026-08-30

## Context

A worker can disappear while executing. Holding a permanent lock sacrifices liveness; immediately retrying risks concurrent duplicate execution.

## Decision

Workers receive time-bounded PostgreSQL leases and renew them while executing. Expired leases are retried. Durable state commits are fenced by lease ownership. Because an expired lease does not prove the old process stopped, external side effects must use stable idempotency/fencing keys where duplicates would be harmful.

## Consequences

Positive: crashed work makes progress without manual intervention; stale workers cannot commit durable results after losing ownership.

Negative: overlapping execution is possible during long pauses/network partitions. Exactly-once external effects are not claimed unless the target system participates through idempotency or a shared transaction boundary.
