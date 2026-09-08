# CODEX Audit

Audit date: 2026-09-08  
Baseline branch: `master`  
Baseline source commit: `9bf12168d30f9407fc7e88eb1c90c201d1f5c73a`

No source, configuration, migration, or test file was changed as part of this investigation.

> **Execution limitation:** the requested local/Work-mode execution environment was declined/unavailable in this session. I therefore do not claim a local shell baseline. The exact baseline below is from GitHub Actions run `34223921288`, whose jobs performed a clean checkout of the exact `master` SHA above and ran the repository's configured checks. A standalone Python vulnerability scan could not be run because none is configured in the repository and arbitrary local command execution was unavailable.

## Section 1: Architecture & tooling summary

### Architecture map

| Component | Stack / entry point | Role and connections |
|---|---|---|
| API | Python 3.13, FastAPI/Pydantic; `uvicorn app.main:app --host 0.0.0.0 --port 8000` | Workflow submit/list/detail/cancel, workers, durable DLQ, health, metrics. PostgreSQL reads/writes; health probes Redis. |
| Workflow engine | `backend/app/engine.py`, async SQLAlchemy | DAG/state transitions, retries, leases, dependency release, cancellation, worker registry, scheduling/recovery. PostgreSQL is source of truth. |
| Worker | asyncio; `python -m app.worker` | Consumes Redis Streams, acquires/renews PostgreSQL leases, executes handlers, persists result/failure, ACKs Redis. |
| Scheduler/outbox | asyncio; `python -m app.scheduler` | Publishes transactional outbox to Redis, releases scheduled/retry work, reaps leases, republishes old queued tasks, marks stale workers, updates metrics. |
| Queue/guards | redis-py, Redis Streams + Lua | Partitioned task streams, consumer group, poison/DLQ streams, token bucket, distributed concurrency guard. |
| Persistence | PostgreSQL 17, SQLAlchemy, Alembic; `alembic -c backend/alembic.ini upgrade head` | Workflows, tasks, events, workers, outbox and effect-record schema. |
| Web console | TypeScript, React 19.1.1, Next.js App Router 15.5.2 | Polls API and renders workflows, DAG graph, tasks, workers, DLQ and timeline. |
| Observability | Prometheus, Grafana, OpenTelemetry Collector | API `/metrics`; worker `:9101`; scheduler `:9102`; Grafana provisioned from Prometheus; OTLP on `4317/4318`. |
| Load/chaos | k6 JavaScript; Bash + Compose | `./scripts/run-benchmark.sh`; `./scripts/chaos-compose.sh`. |
| CI | `.github/workflows/ci.yml` | Ruff/mypy/tests, PostgreSQL+Redis integration, web type/build, Docker builds, destructive recovery scenarios. |

### Data/control flow

1. Client POSTs a DAG to FastAPI.
2. `submit_workflow()` validates it and commits workflow/tasks plus `task.ready` outbox records to PostgreSQL.
3. Scheduler publishes unpublished outbox records into partitioned Redis Streams.
4. Worker receives a stream record and atomically acquires the PostgreSQL lease; DB state/lease fencing protects against duplicate deliveries.
5. Worker executes, renews the lease/concurrency reservation, persists success/failure, then ACKs Redis.
6. Success releases dependency-ready children; retries/scheduling use `available_at`; terminal failures use durable task state plus Redis DLQ mirroring.
7. Next.js polls FastAPI; Prometheus/Grafana and OpenTelemetry observe the runtime.

### Package managers

- Python: `pip` / setuptools via root `pyproject.toml`.
- Frontend: `npm` via `web/package.json`; **no `web/package-lock.json` is committed**.
- Infrastructure: Docker Compose.
- Load testing: external k6 binary.

### Exact install/run/lint/test commands

Full stack:

```bash
cp .env.example .env
docker compose up --build
```

Python development:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check .
mypy backend/app
pytest -q backend/tests/unit
pytest -q -m property backend/tests/property
pytest -q -m chaos backend/tests/chaos
```

Integration:

```bash
docker compose up -d postgres redis
alembic -c backend/alembic.ini upgrade head
pytest -q -m integration backend/tests/integration
```

Frontend:

```bash
cd web
npm install
npm run dev
npm run lint   # currently tsc --noEmit, not ESLint
npm run build
npm run start
```

Repository Make targets:

```bash
make install
make lint
make typecheck
make unit
make integration
make chaos
make test
make up
make down
make benchmark
```

`make down` expands to `docker compose down -v` and deletes the Compose PostgreSQL/Redis volumes. `make unit` expands to `pytest -m 'not integration and not chaos'`, which also includes the `property` suite (DEP-05).

Load test:

```bash
./scripts/run-benchmark.sh
```

## Section 2: Baseline test/lint results (the "before" numbers)

### Provenance

GitHub Actions run `34223921288` cleanly checked out `master` at `9bf12168d30f9407fc7e88eb1c90c201d1f5c73a`. All five CI jobs concluded `success`.

| Check | Exact command | Result |
|---|---|---|
| Python install | `python -m pip install -e '.[dev]'` | PASS on CPython 3.13.15 |
| Ruff | `ruff check .` | **0 violations**, PASS |
| mypy | `mypy backend/app` | **0 errors in 14 source files**, PASS |
| Unit | `pytest -q backend/tests/unit` | **8 passed, 0 failed, 0 errors** |
| Property | `pytest -q -m property backend/tests/property` | **1 passed, 0 failed, 0 errors** |
| Recovery model | `pytest -q -m chaos backend/tests/chaos` | **2 passed, 0 failed, 0 errors** |
| Migration | `alembic -c backend/alembic.ini upgrade head` | PASS against PostgreSQL 17 |
| Integration | `pytest -q -m integration backend/tests/integration` | **6 passed, 0 failed, 0 errors** |
| Python test total | four non-overlapping pytest invocations above | **17 passed, 0 failed, 0 errors** |
| npm install/audit signal | `npm install` | install succeeds; **3 vulnerabilities: 1 Critical, 2 High** |
| Web type-check | `npm run lint` -> `tsc --noEmit` | **0 TypeScript errors**, PASS |
| Web build | `npm run build` | PASS with **1 Autoprefixer warning** (`align-items:end`) |
| Docker build | `docker compose build api worker scheduler web` | PASS |
| Destructive recovery | `./scripts/chaos-compose.sh` | PASS; log ends `All Docker chaos/recovery scenarios passed.` |

Dependency baseline:

- Clean npm install audited 28 packages and explicitly warned that `next@15.5.2` is vulnerable, with **1 Critical + 2 High** vulnerabilities in the resolved graph.
- Upstream Next.js guidance checked on 2026-09-08 lists 15.x as Maintenance LTS; the August 25, 2026 security release says to upgrade the 15.x line to `15.5.24`. Sources: `https://nextjs.org/blog` and `https://nextjs.org/blog/CVE-2025-66478`.
- No `pip-audit`/equivalent Python vulnerability gate is configured, so no Python CVE count is claimed.

Coverage baseline:

- No coverage package/configuration/threshold exists. Exact line/branch coverage and exact zero-covered-line counts therefore cannot be proven from the repository. Section 3 instead identifies modules/features with no direct tests; the Compose chaos harness indirectly exercises parts of the runtime.

## Section 3: Findings

### A. Correctness & distributed-systems behavior

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| COR-01 | `backend/app/engine.py` | `reap_expired_leases()` can move a final-attempt task to `dead_letter` without recomputing its workflow, leaving the workflow `running` forever. | High | Small |
| COR-02 | `backend/app/config.py`, `backend/app/worker.py` | Default `worker_id='worker-local'` makes the hostname/PID fallback unreachable in normal defaults. Multiple workers without explicit IDs share worker row, Redis consumer name and lease-owner identity. | High | Small |
| COR-03 | `backend/app/worker.py` | Lease renewal stops when global shutdown starts even though graceful shutdown waits for active tasks; a draining task can lose its lease while still executing. | High | Small |
| COR-04 | `backend/app/queue.py`, `backend/app/worker.py` | Consumer reads only `XREADGROUP ... '>'`; there is no `XAUTOCLAIM`/`XCLAIM`. A crash-before-ACK leaves a PEL record that is never reclaimed/ACKed, even if DB lease recovery creates a replacement message. | High | Medium |
| COR-05 | `backend/app/queue.py`, `backend/app/worker.py` | Poison handling catches missing fields but not malformed UUID values. Invalid `task_id` can raise during processing, remain pending and never reach poison DLQ. | Medium | Small |
| COR-06 | `backend/app/engine.py` | After a prerequisite terminally fails/dead-letters, downstream dependent tasks can remain `pending` inside a terminal failed workflow instead of becoming explicit terminal skipped/cancelled tasks. | Medium | Medium |
| COR-07 | `backend/app/worker.py` | A transient DB error exits `heartbeat_loop()` permanently while the main worker keeps processing, eventually making a live worker appear stale. | Medium | Small |
| COR-08 | `backend/app/worker.py` | Detached `process_message()` tasks are only removed from a set; their exceptions are not retrieved/logged, allowing infrastructure/programming errors to surface only as unobserved-task warnings. | Medium | Small |
| COR-09 | `backend/app/queue.py` | Shared token-bucket timestamps come from each worker's `time.time()`, so host clock skew can over/under-refill a distributed rate limit. | Medium | Medium |
| COR-10 | `backend/app/models.py`, `backend/app/engine.py` | Runtime state `scheduled` is represented by raw string literals but is absent from `TaskState`, weakening state consistency/type checking. | Low | Small |
| COR-11 | `backend/app/engine.py` | `workflow_query()` is an apparently unused `async` helper with no await that only constructs a SQLAlchemy select. | Low | Small |

### B. Outbox, resilience & observability

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| RES-01 | `backend/app/scheduler.py` | Outbox publisher always selects oldest unpublished 100. Enough permanently unpublishable rows can occupy the head and starve newer valid events; no backoff/quarantine/max-attempt state exists. | High | Large |
| RES-02 | `backend/app/scheduler.py` | Outbox publish failures and the outer scheduler loop broadly catch `Exception` without logging/error metrics, making persistent outages or code defects operationally invisible. | Medium | Small |
| RES-03 | `backend/app/scheduler.py` | Redis failure during queue-depth calculation is converted to depth `0`, so an outage can be graphed as an empty/healthy queue. | Medium | Small |
| RES-04 | `backend/app/scheduler.py` | `publish_batch()` holds PostgreSQL row locks/transaction state while awaiting Redis network I/O across a batch, coupling Redis latency/outage to DB lock duration. | Medium | Large |

### C. Security

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| SEC-01 | `web/package.json`, resolved npm graph | `next@15.5.2` is vulnerable. Clean CI reports **3 npm vulnerabilities (1 Critical, 2 High)**. Same-line patched Maintenance-LTS release is at least `15.5.24` as of the audit date. | Critical | Small |
| SEC-02 | `docker-compose.yml` | PostgreSQL (`workflow/workflow`), passwordless Redis, Grafana (`admin/admin`) and other service ports are published on all host interfaces by default instead of loopback-only. | High | Small |
| SEC-03 | `backend/app/main.py` | No API authentication/authorization; any reachable caller can submit/list/read/cancel workflows and inspect worker/DLQ data. A real fix requires an identity/tenant/authorization contract. | High | Large |
| SEC-04 | `backend/app/schemas.py`, `backend/app/main.py` | Workflow task count is bounded, but arbitrary nested task payload bytes/depth and HTTP request size are not explicitly bounded; an exposed anonymous endpoint can consume excessive memory/DB storage. | High | Medium |

No hardcoded production API keys/secrets were found. The Compose credentials are clearly development defaults, but broad port binding turns them into an exposure risk. CORS is not globally permissive: only `http://localhost:3000` is allowed, so no separate CORS finding is recorded.

### D. Performance & resource management

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| PERF-01 | `backend/app/queue.py` | Task/event/poison/DLQ Redis Streams use unbounded `XADD` with no retention/trim policy; long-lived deployments can grow Redis memory/disk indefinitely. Safe trimming must preserve PEL and DLQ semantics. | High | Large |
| PERF-02 | `backend/app/engine.py`, `backend/app/scheduler.py` | Old `queued` DB tasks are blindly republished based on `updated_at` without checking whether a valid Redis message is merely waiting/backlogged; sustained delay can manufacture duplicate messages repeatedly. | High | Large |
| PERF-03 | `backend/app/engine.py` | Each task success locks/loads all workflow tasks and scans dependencies/children. For DAGs up to 1000 tasks, aggregate work trends toward O(N²) and increases lock contention. | Medium | Medium |
| PERF-04 | `backend/app/main.py` | Workflow detail returns the entire timeline with no cursor/limit; long-lived/retry-heavy workflows make each polling response/query grow without bound. | Medium | Small |
| PERF-05 | `backend/app/main.py` | `/healthz` creates/closes a new Redis client per request instead of reusing the existing application Redis connection/pool. | Low | Small |
| PERF-06 | `web/app/page.tsx` | Polling is fixed at 2.5s/1.5s and the global poll effect restarts on every workflow selection, adding avoidable API/DB load. | Low | Medium |

Relevant task scheduling/reaping predicates have composite indexes and workflow task/event access is indexed; no obvious API N+1 query was found.

### E. Frontend correctness

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| WEB-01 | `web/app/page.tsx` | Selecting another workflow does not clear old `detail`; until the new fetch succeeds — indefinitely on failure — stale details from the previous workflow can remain visible. | Medium | Small |
| WEB-02 | `web/app/page.tsx` | Global and detail polling share one `error`; a successful global refresh clears it and can hide an ongoing detail-fetch failure. | Low | Small |

No duplicate frontend routes or obviously non-functional controls were found. The console is monitoring-only, so lack of mutation buttons is not treated as a stub.

### F. Dependencies, reproducibility & lint/tooling

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| DEP-01 | `web/package.json`, `web/Dockerfile`, `.github/workflows/ci.yml` | No committed `web/package-lock.json`; CI/Docker use `npm install`, so identical source can resolve different transitive dependencies and `npm ci` cannot be used. | Medium | Small |
| DEP-02 | `pyproject.toml`, `.github/workflows/ci.yml` | No Python dependency vulnerability scanner is installed/run in CI. | Medium | Small |
| DEP-03 | `web/package.json` | `npm run lint` is only `tsc --noEmit`; there is no ESLint/React source linting. | Low | Small |
| DEP-04 | `web/app/globals.css` | Next build emits Autoprefixer warning for `align-items:end`; `flex-end` is the compatible spelling. | Low | Small |
| DEP-05 | `Makefile`, pytest markers | `make unit` selects every test not marked integration/chaos, so it includes property tests even though CI treats property tests separately. | Low | Small |
| DEP-06 | `pyproject.toml`, `backend/app/engine.py` | Entire `engine.py` suppresses Ruff `F401`, hiding the current unused `and_` import and potentially future unused imports. | Low | Small |

### G. Dead/incomplete code

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| DEAD-01 | `backend/app/config.py`, `backend/app/scheduler.py`, env/Compose config | `outbox_poll_seconds` is configurable but never consumed; scheduler sleeps only `scheduler_poll_seconds`. | Low | Small |
| DEAD-03 | `backend/app/models.py`, initial Alembic migration | `EffectRecord`/`effect_records` exists in the durable schema but no runtime engine/worker path uses it. Removing it needs a migration; making it authoritative needs a deliberate side-effect/idempotency design. | Low | Large |

Repository-wide searches found **no `TODO` or `FIXME` comments** and no duplicate FastAPI route definitions. The explicit `noop`/`sleep`/`flaky` handlers and simulator/test data are clearly demo/test mechanisms rather than fake production integrations being represented as real integrations.

### H. Test coverage gaps

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| TEST-01 | `backend/app/main.py` | No direct FastAPI/httpx route tests for validation, 404s, idempotency header behavior, cancellation route, worker/DLQ endpoints, health, CORS or serialization. | Medium | Medium |
| TEST-02 | `backend/app/queue.py`, `backend/app/worker.py`, `backend/app/scheduler.py` | No focused tests for PEL reclaim, malformed poison records, Lua guards, graceful shutdown, timeout/unknown handlers, lease loss, heartbeat failure recovery, outbox partial failure or Redis-metrics failure. Chaos tests cover some runtime behavior indirectly. | Medium | Medium |
| TEST-03 | `web/` | No frontend unit/component/E2E framework or tests are configured. | Medium | Medium |
| TEST-04 | `pyproject.toml`, `.github/workflows/ci.yml` | No line/branch coverage collection or CI threshold exists. | Medium | Small |
| TEST-05 | `backend/alembic/`, `.github/workflows/ci.yml` | CI checks upgrade-to-head on a fresh DB but not migration downgrade/upgrade round trip or model-vs-migration drift. | Low | Medium |

## Section 4: Total counts

**40 unique findings total:**

- **Critical: 1**
- **High: 10**
- **Medium: 17**
- **Low: 12**

Highest-priority safe fixes: SEC-01, COR-01, COR-02, COR-03, SEC-02 and COR-04. Highest-risk design items to treat separately: SEC-03, SEC-04, RES-01, RES-04, PERF-01, PERF-02 and DEAD-03.