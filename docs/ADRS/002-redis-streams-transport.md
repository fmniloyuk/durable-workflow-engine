# ADR 002: Redis Streams as at-least-once transport

- Status: Accepted
- Date: 2026-08-30

## Context

The project needs a realistic queue with partitions, consumer groups, explicit ACKs, and failure behavior while remaining easy to run locally.

## Decision

Use Redis Streams partitioned by a stable hash of task ID. Treat stream delivery as at-least-once and tolerate duplicates. Do not use Redis as the workflow state authority.

## Consequences

Positive: simple local operation; realistic consumer groups; cheap rate/concurrency coordination; easy chaos testing.

Negative: Redis durability depends on deployment configuration; stream retention must be managed in production; consumer PEL cleanup is operational work. The scheduler's durable republisher exists so PEL claiming is not required for correctness.
