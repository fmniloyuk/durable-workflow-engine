# CODEX Plan

Derived from `CODEX_AUDIT.md` on 2026-09-08. No source/config/test changes are part of this planning step.

Ranking principle: **impact × ease of a safe fix**, not severity alone. Global rank `1` is the highest execution priority. Large/risky architecture work is intentionally ranked below smaller fixes that remove immediate correctness/security risk.

> **Human-review gate before implementation:** the items in Section 3 are intentionally excluded from the immediate batch when they change public contracts, database/retention semantics, authorization boundaries, or core delivery guarantees. Approve those design decisions before implementing them.

## Section 1: Do Now

These are the Critical/High Small-or-Medium fixes that can be made safely without a contract/schema redesign, plus high-value low-risk quick wins and regression coverage needed to protect them.

### 1. [Global rank 1] SEC-01 — Vulnerable Next.js dependency

**Finding:** `web/package.json` pins vulnerable `next@15.5.2`; clean npm install reports 1 Critical + 2 High vulnerabilities.

**Proposed fix:** Upgrade within the existing 15.5 Maintenance-LTS line to at least `15.5.24` (current security release at audit time), regenerate the resolved dependency graph, run `npm audit`, TypeScript check and production build, and resolve any remaining High/Critical findings without a major Next.js migration.

**Likely files:** `web/package.json`, `web/package-lock.json` (created), possibly `web/` source only if the patched release exposes a compatibility issue.

### 2. [Global rank 2] COR-01 — Lease-expiry terminal failure can leave workflow running

**Finding:** final-attempt lease expiry can set a task to `dead_letter` without recomputing workflow state.

**Proposed fix:** In the expired-lease transaction, collect/lock the affected workflow and its tasks and invoke the same workflow-state recomputation used by normal terminal failure. Add an integration regression test asserting final lease expiry makes the workflow `failed`.

**Likely files:** `backend/app/engine.py`, `backend/tests/integration/test_engine_semantics.py`.

### 3. [Global rank 3] COR-03 — Graceful shutdown stops lease renewal too early

**Finding:** shutdown sets `self.stop`, which terminates lease renewal while active work is still allowed to drain.

**Proposed fix:** Stop accepting new work on shutdown, but keep each active task's lease/concurrency renewal loop alive until that task sets its local `done` event or is explicitly cancelled. Add a focused async test with a task whose drain time exceeds one renewal interval.

**Likely files:** `backend/app/worker.py`, worker-focused tests under `backend/tests/`.

### 4. [Global rank 4] COR-02 — Default worker identity collides when scaling

**Finding:** default `worker_id='worker-local'` causes multiple unconfigured worker processes to share identity.

**Proposed fix:** Make configured worker ID optional; when absent, generate a process-unique runtime ID from hostname + PID + short UUID. Preserve explicit `WORKER_ID` for operators who intentionally provide a unique stable identity.

**Likely files:** `backend/app/config.py`, `backend/app/worker.py`, config/worker tests, `.env.example` if it currently implies the literal default.

### 5. [Global rank 5] SEC-02 — Compose publishes trusted local services broadly

**Finding:** PostgreSQL/Redis/Grafana/API/metrics/OTLP ports bind all host interfaces while using development credentials/no auth.

**Proposed fix:** Bind host-published development ports to `127.0.0.1` by default and remove host publication entirely for ports not needed from the host. Keep inter-container traffic on the Compose network unchanged.

**Likely files:** `docker-compose.yml`, README only if endpoint documentation needs clarification.

### 6. [Global rank 6] COR-04 — Redis PEL entries are never reclaimed

**Finding:** only new stream entries are read; stale pending entries from crashed consumers are never claimed/ACKed.

**Proposed fix:** Add a bounded stale-message reclaim path using `XAUTOCLAIM` per queue partition with an idle threshold aligned to lease recovery. Feed claimed messages through the same DB lease gate; ACK messages whose DB task is already terminal/not acquirable, and test crash-before-ACK recovery plus PEL cleanup.

**Likely files:** `backend/app/queue.py`, `backend/app/worker.py`, `backend/tests/integration/`, `backend/tests/chaos/` and/or `scripts/chaos-compose.sh`.

### 7. [Global rank 8] COR-05 — Malformed UUID messages bypass poison handling

**Finding:** fields-present invalid UUIDs can raise during worker processing and remain pending.

**Proposed fix:** Validate UUID-bearing transport fields before creating executable work; classify invalid identifiers as poison, copy them to the poison stream with a reason, and ACK the original message so it cannot poison the PEL indefinitely.

**Likely files:** `backend/app/queue.py`, `backend/app/worker.py`, queue/worker tests.

### 8. [Global rank 9] COR-07 — Worker heartbeat does not recover from transient DB errors

**Finding:** one heartbeat exception permanently kills the heartbeat coroutine.

**Proposed fix:** Catch transient heartbeat failures inside the loop, log/metric them, back off for a bounded interval, and continue until shutdown. Do not let heartbeat failure silently terminate the coroutine.

**Likely files:** `backend/app/worker.py`, `backend/app/metrics.py`, worker tests.

### 9. [Global rank 10] COR-08 — Background processing exceptions are unobserved

**Finding:** detached `process_message()` task exceptions are never retrieved.

**Proposed fix:** Replace the discard-only callback with a completion handler that removes the task, retrieves `task.exception()` safely, and emits structured error logging/metrics; alternatively use a managed `TaskGroup` if it can preserve current worker concurrency semantics.

**Likely files:** `backend/app/worker.py`, worker tests.

### 10. [Global rank 11] RES-02 — Scheduler/outbox failures are swallowed

**Finding:** broad exception handling has no operational signal.

**Proposed fix:** Add module-level structured logging and explicit scheduler/outbox failure counters. Preserve retry behavior, but log exception context/event IDs so persistent failures are diagnosable without changing delivery semantics.

**Likely files:** `backend/app/scheduler.py`, `backend/app/metrics.py`, scheduler tests.

### 11. [Global rank 12] RES-03 — Redis outage is reported as queue depth zero

**Finding:** metric calculation converts Redis errors to a false healthy-looking `0`.

**Proposed fix:** Preserve last-known depth or emit `NaN`/an explicit `queue_metrics_up=0` health gauge on Redis failure; log the failure. Never synthesize zero from an unavailable transport.

**Likely files:** `backend/app/scheduler.py`, `backend/app/metrics.py`, scheduler tests, Grafana dashboard if an availability panel is added.

### 12. [Global rank 13] DEP-01 — No npm lockfile / nondeterministic web builds

**Finding:** no `web/package-lock.json`; CI and Docker use `npm install`.

**Proposed fix:** Generate and commit the lockfile after SEC-01, switch CI and Docker dependency installation to `npm ci`, and use the lockfile as the setup-node cache key input.

**Likely files:** `web/package-lock.json`, `web/Dockerfile`, `.github/workflows/ci.yml`.

### 13. [Global rank 14] DEP-02 — No Python dependency vulnerability gate

**Finding:** Python dependencies are installed but not vulnerability-scanned.

**Proposed fix:** Add `pip-audit` to the CI security/quality path (or a dedicated dependency job), scan the installed project dependencies on every PR, and fail on actionable vulnerabilities after resolving any baseline findings rather than adding blanket ignores.

**Likely files:** `pyproject.toml` and/or `.github/workflows/ci.yml`.

### 14. [Global rank 15] WEB-01 — Workflow selection can show stale detail

**Finding:** old workflow detail remains visible until a new detail request succeeds.

**Proposed fix:** Clear detail immediately when selection changes and use request cancellation/selection identity checks so a slow prior fetch cannot overwrite the newly selected workflow.

**Likely files:** `web/app/page.tsx`, frontend tests when TEST-03 is addressed.

### 15. [Global rank 16] TEST-04 — No coverage collection or floor

**Finding:** CI has no measurable line/branch coverage signal.

**Proposed fix:** Add `pytest-cov`, collect branch/line coverage for the Python suites, publish the report in CI, establish the current measured baseline, then set a non-regression floor at or just below that measured baseline rather than guessing a percentage.

**Likely files:** `pyproject.toml`, `.github/workflows/ci.yml`.

### 16. [Global rank 18] COR-09 — Distributed rate limiter trusts worker clocks

**Finding:** token-bucket refill uses client wall-clock timestamps.

**Proposed fix:** Use Redis server `TIME` inside the Lua script as the shared clock and remove client timestamp ARGV. Add tests covering monotonic refill behavior independent of mocked worker clocks.

**Likely files:** `backend/app/queue.py`, queue integration tests.

### 17. [Global rank 20] TEST-02 — Missing direct queue/worker/scheduler edge tests

**Finding:** the most failure-sensitive runtime components rely heavily on indirect chaos coverage.

**Proposed fix:** Add focused tests alongside COR-03/04/05/07/08 and RES-02/03: PEL reclaim, malformed poison, graceful drain, lost lease, heartbeat retry, background exception observation, rate/concurrency guards and scheduler failure metrics.

**Likely files:** new/expanded tests under `backend/tests/unit/` and `backend/tests/integration/`.

### 18. [Global rank 29] PERF-05 — Health check creates a Redis client per request

**Finding:** `/healthz` repeatedly creates/closes a client despite an existing queue Redis connection.

**Proposed fix:** Reuse the application/queue backend's connection pool/client for `PING`, with lifecycle cleanup handled once at shutdown. Keep health behavior and response schema unchanged.

**Likely files:** `backend/app/main.py`, possibly `backend/app/queue.py`, API tests.

### 19. [Global rank 30] WEB-02 — Shared polling error state masks failures

**Finding:** success in one poll can clear another poll's failure.

**Proposed fix:** Split global-list and workflow-detail error state (or model request status by resource) and render the relevant error independently.

**Likely files:** `web/app/page.tsx`.

### 20. [Global rank 31] DEP-03 — Frontend "lint" is only TypeScript checking

**Finding:** no ESLint/React lint layer exists.

**Proposed fix:** Add ESLint with the Next/React-compatible config for the patched Next release, introduce a separate `typecheck` script, and have CI run both lint and typecheck explicitly.

**Likely files:** `web/package.json`, `web/package-lock.json`, ESLint config, `.github/workflows/ci.yml`.

### 21. [Global rank 32] DEP-04 — Autoprefixer warning

**Finding:** `align-items:end` produces a production-build compatibility warning.

**Proposed fix:** Replace it with `align-items:flex-end` and require a warning-free web build for this known warning.

**Likely files:** `web/app/globals.css`.

### 22. [Global rank 33] DEP-05 — Local unit target includes property suite

**Finding:** local Make target does not match CI suite boundaries.

**Proposed fix:** Make `unit` target the explicit unit directory, add/retain a distinct `property` target, and include both intentionally in `test` so local and CI commands communicate the same suite structure.

**Likely files:** `Makefile`.

### 23. [Global rank 34] DEP-06 — Broad Ruff F401 suppression

**Finding:** whole-file suppression hides the current unused `and_` import and future dead imports.

**Proposed fix:** Remove the unused import and the whole-file `F401` exception; keep only narrow type-checker suppressions that are actually required by third-party typing limitations.

**Likely files:** `backend/app/engine.py`, `pyproject.toml`.

### 24. [Global rank 35] COR-10 — `scheduled` missing from TaskState

**Finding:** a real task state is encoded as raw strings instead of the existing state constants.

**Proposed fix:** Add `SCHEDULED = 'scheduled'` to `TaskState` and replace matching runtime string literals with the enum constant. Since the DB column is a string, this does not require a schema migration.

**Likely files:** `backend/app/models.py`, `backend/app/engine.py`, tests that assert scheduled state transitions.

### 25. [Global rank 36] COR-11 — Dead async `workflow_query()` helper

**Finding:** unused query-builder helper is misleading and unnecessarily async.

**Proposed fix:** Remove the helper after confirming repository-wide no references; do not replace it with another abstraction unless a caller actually needs it.

**Likely files:** `backend/app/engine.py`.

### 26. [Global rank 37] DEAD-01 — Unused `outbox_poll_seconds` setting

**Finding:** configuration advertises a value that scheduler never consumes.

**Proposed fix:** Remove the unused setting and its environment/Compose documentation so there is one truthful scheduler polling control. If independent outbox cadence is desired later, implement it as a deliberate scheduler design rather than retaining dead config.

**Likely files:** `backend/app/config.py`, `.env.example`, `docker-compose.yml`, documentation if referenced.

## Section 2: Deferred

These items are ranked globally but deliberately kept out of the immediate batch because they are Large, alter a public contract/state model, require schema/data-retention decisions, or are better justified by benchmarks/product requirements first.

| Global rank | Finding | Reason deferred |
|---:|---|---|
| 7 | **SEC-04 — Request/payload size is unbounded** | A body/payload cap is important, but choosing the byte/depth ceiling changes the public submission contract; agree limits and deployment/proxy ownership before enforcing them. |
| 17 | **PERF-04 — Unbounded workflow timeline response** | Pagination/limits are straightforward technically but change the existing response contract; define cursor/limit compatibility and UI behavior first. |
| 19 | **COR-06 — Downstream tasks remain pending after terminal prerequisite failure** | Fix changes externally visible task-state semantics; decide whether descendants should be `skipped`, `cancelled`, or a new terminal state before implementation. |
| 21 | **TEST-01 — No direct FastAPI route suite** | Valuable but Medium effort and not required to land the highest-risk distributed fixes; schedule immediately after the correctness/security batch, ideally after API-contract decisions above. |
| 22 | **PERF-03 — O(N²)-like full-workflow scan/locking on success** | Refactoring dependency readiness touches concurrency/locking correctness; benchmark large DAGs and design the child/dependency index strategy before changing it. |
| 23 | **TEST-03 — No frontend test framework** | Medium setup effort; choose component vs browser/E2E coverage based on how much the monitoring UI will evolve before introducing the stack. |
| 24 | **RES-01 — Poison outbox rows can starve newer events** | Robust fix likely needs retry metadata/backoff/quarantine state and therefore a schema/delivery-semantics decision. |
| 25 | **PERF-01 — Redis Streams have no retention policy** | Trimming can destroy data still needed by consumer groups or DLQ operators; define PEL-safe retention and DLQ retention/SLOs first. |
| 26 | **PERF-02 — Blind orphan republishing can amplify duplicates** | Correct fix is coupled to PEL reclaim/outbox publication semantics; land COR-04 first, observe behavior, then redesign recovery without creating another duplicate/loss window. |
| 27 | **SEC-03 — No API authentication/authorization** | Requires product/security decisions for identity, tenants, service-to-service auth and authorization; this is not a safe patch-level change. |
| 28 | **RES-04 — DB locks held across Redis publication** | Changing transaction boundaries directly affects transactional-outbox delivery guarantees and data integrity; needs an ADR/design review before code. |
| 38 | **TEST-05 — Migration downgrade/drift checks absent** | Useful hardening but lower immediate impact; add when the next schema migration is introduced so the round-trip/drift harness is validated against a real migration change. |
| 39 | **PERF-06 — Aggressive polling / restart-on-selection** | Optimization choice depends on intended console scale and whether to keep polling, use SSE, or use WebSockets; not worth an architecture change before usage data. |
| 40 | **DEAD-03 — Unused `EffectRecord` schema** | Removing it changes the DB schema; using it changes side-effect/idempotency guarantees. Decide its intended role first, then either migrate it out or write an ADR and implement it. |

## Section 3: Ambiguous/risky items requiring human review

The following decisions should be made explicitly before their deferred implementation:

1. **SEC-03 — Authentication/authorization model:** Is this engine intentionally localhost/internal-only, or should it support public/multi-tenant operation? If multi-tenant, define tenant ownership of workflows/tasks and which identities may submit/read/cancel.
2. **SEC-04 — Submission size contract:** Choose an accepted maximum HTTP body, per-task payload size/depth, and whether enforcement belongs in FastAPI, reverse proxy, or both.
3. **COR-06 — Descendant terminal semantics:** Choose `skipped`, `cancelled`, or a new state for tasks made impossible by an upstream terminal failure; document whether this is part of the public state contract.
4. **PERF-04 — Timeline API pagination:** Decide whether to add query parameters/cursors while preserving the current full `timeline` field or introduce a versioned response.
5. **RES-01 — Outbox poison policy:** Decide retry count/backoff, quarantine representation, operator replay behavior and whether schema fields such as `attempt_count`, `next_attempt_at`, `last_error`, or terminal state are acceptable.
6. **PERF-01 — Redis retention:** Define work-stream retention, PEL safety, event-stream retention and poison/DLQ retention. Do not apply a generic `MAXLEN` blindly.
7. **PERF-02 — Orphan-republish design:** After implementing PEL reclaim, decide whether DB queued-task scanning remains necessary and what durable signal proves a task currently lacks a transport hint.
8. **RES-04 — Outbox transaction boundary:** Any reduction in DB-lock duration must preserve the documented at-least-once/ambiguous-publish behavior; capture the chosen algorithm in an ADR before implementation.
9. **DEAD-03 — `EffectRecord` intent:** Decide whether it is meant to provide local idempotency for side effects, is educational schema only, or should be removed. The existing documentation correctly notes that a local record cannot guarantee exactly-once behavior against arbitrary remote services.
10. **PERF-03 / PERF-06 — Performance targets:** Define large-DAG and console-client scale targets before committing to dependency-index or realtime transport redesigns.

### Global ranking completeness

All **40** findings from `CODEX_AUDIT.md` appear exactly once above: **26 Do Now + 14 Deferred**. The global rank numbers intentionally have gaps within each section because they rank the combined set by impact × safe-fix ease.