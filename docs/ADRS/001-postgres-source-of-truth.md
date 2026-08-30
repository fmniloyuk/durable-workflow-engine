# ADR 001: PostgreSQL is the workflow source of truth

- Status: Accepted
- Date: 2026-08-30

## Context

Redis Streams provides useful delivery and consumer-group primitives but is not sufficient as the sole durable workflow state model. Recovery needs task attempts, leases, dependency state, cancellation, results, and idempotency to survive queue/process restarts.

## Decision

Persist every authoritative workflow/task transition in PostgreSQL. Redis entries contain task identifiers and trace context only. Workers must conditionally acquire a PostgreSQL lease before execution.

## Consequences

Positive: queue duplication/loss can be repaired from durable state; monitoring and reconstruction do not depend on Redis history; transactional outbox is possible.

Negative: PostgreSQL becomes a throughput/availability dependency; hot workflows can contend on rows; large-scale dependency release would require a more normalized edge model.
