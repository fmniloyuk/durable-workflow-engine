# CODEX Audit

Audit date: 2026-09-08

Baseline branch: `master`

Baseline commit audited: `9bf12168d30f9407fc7e88eb1c90c201d1f5c73a`

Scope: architecture/tooling, clean-baseline checks, correctness/distributed-systems behavior, security, performance/resource use, dependencies, dead/incomplete code, frontend behavior, and test gaps. No source/config/test files were changed during this audit.

> **Baseline execution note:** the requested local/Work-mode execution environment was not available in this session, so I did **not** claim a local shell run. The baseline below comes from GitHub Actions run `34223921288`, which performed a clean `actions/checkout` of the exact audited `master` SHA above and ran the repository's configured checks. Repository source review was performed against the same SHA. A separate Python `pip-audit` could not be executed because it is not configured in the repository and arbitrary local shell execution was unavailable.

## Section 1: Architecture & tooling summary

### Architecture map

| Layer / service | Language / framework | Entry point | Responsibility / connections |
|---|---|---|---|
| API | Python 3.13, FastAPI, Pydantic | `uvicorn app.main:app --host 0.0.0.0 --port 8000` (`backend/app/main.py`) | Accepts workflow submissions, serves workflow/task/timeline state, cancellation, worker status, dead-letter state, health and Prometheus metrics. Reads/writes PostgreSQL. Health also probes Redis. |
| Workflow engine/domain | Python, SQLAlchemy async | `backend/app/engine.py` | DAG submission, task state transitions, leases, retries, dependency release, cancellation, worker registration/heartbeats, schedule release, expired-lease recovery, orphan republishing. PostgreSQL is the authoritative state store. |
| Worker | Python, asyncio | `python -m app.worker` (`backend/app/worker.py`) | Reads Redis Streams, obtains PostgreSQL leases, runs task handlers, renews leases, persists results/failures, ACKs messages, exposes worker metrics. |
| Scheduler/outbox publisher | Python, asyncio | `python -m app.scheduler` (`backend/app/scheduler.py`) | Publishes transactional outbox events to Redis, releases scheduled/retry tasks, reaps leases, republishes old queued tasks, marks stale workers, updates metrics. |
| Queue/rate/concurrency layer | Python, redis-py, Redis Streams + Lua | `backend/app/queue.py` | Partitioned streams, consumer group, publishing/ACK, poison/dead-letter streams, token-bucket rate limiting, distributed concurrency guard. |
| Persistence | PostgreSQL 17, SQLAlchemy, Alembic | `alembic -c backend/alembic.ini upgrade head` | Durable workflow/task/event/worker/outbox/effect tables. Migration entry under `backend/alembic`. |
| Transport/coordination | Redis 8 | Docker service `redis` | Redis Streams delivery hints, consumer group state, rate-limit buckets, concurrency zsets, Redis-side DLQ mirrors. PostgreSQL remains source of truth. |
| Monitoring UI | TypeScript, React 19.1.1, Next.js App Router 15.5.2 | `npm run dev` / `npm run start` in `web/` | Polls API for workflows, details, workers and dead-letter tasks; renders dependency graph, task table, worker status and execution timeline. |
| Metrics | Python Prometheus client, Prometheus 3.5 | API `/metrics`; worker `:9101`; scheduler `:9102` | Queue depth, task outcomes/latency/retries, worker utilization, DLQ and outbox metrics; Prometheus scrapes services. |
| Dashboards | Grafana 12.1.1 | Docker service `grafana` on `:3001` | Provisioned Prometheus datasource/dashboard. |
| Tracing | OpenTelemetry SDK/instrumentation + OTel Collector | `backend/app/telemetry.py`, collector `:4317/:4318` | FastAPI/SQLAlchemy/Redis instrumentation plus worker/outbox spans and W3C trace context through Redis. |
| Load testing | JavaScript, k6 | `./scripts/run-benchmark.sh` -> `load/k6-workflows.js` | Constant-arrival-rate workflow submission and result summary output. |
| Chaos/recovery harness | Bash + Docker Compose | `./scripts/chaos-compose.sh` | Destructive recovery scenarios around worker/Redis/API failure, retries, duplicates and idempotency. |
| CI | GitHub Actions YAML | `.github/workflows/ci.yml` | Python quality/tests, PostgreSQL+Redis integration, web type/build, Docker builds, destructive Compose chaos. |

### Main data/control flow

1. Client POSTs a DAG to FastAPI.
2. `submit_workflow()` validates the DAG and commits workflow/tasks plus `task.ready` rows to PostgreSQL's transactional outbox.
3. Scheduler selects unpublished outbox rows and publishes task hints into partitioned Redis Streams.
4. Worker consumes a stream message, then atomically acquires a PostgreSQL task lease. The database lease/state check, not Redis delivery alone, fences duplicate execution attempts.
5. The worker executes the handler, renews its lease/concurrency reservation, commits success/failure to PostgreSQL, and ACKs the Redis message.
6. Success releases dependency-satisfied children; retry/scheduling is persisted through `available_at`; terminal failures use durable task state plus a Redis DLQ mirror.
7. Next.js polls FastAPI; Prometheus/Grafana and OpenTelemetry observe the runtime.

### Package managers and dependency definition

- Python: `pip` / setuptools via root `pyproject.toml`; editable development install is supported.
- Frontend: `npm` via `web/package.json`. **No `web/package-lock.json` is committed.**
- Infrastructure: Docker Compose pulls/builds service images.
- Load tooling: k6 is an external prerequisite for the benchmark script.

### Exact local commands documented/configured by the repository

#### Full stack

```bash
cp .env.example .env
docker compose up --build
```

Useful endpoints after startup:

- API: `http://localhost:8000`
- Web: `http://localhost:3000`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001`

Stop/reset command currently exposed by the Makefile:

```bash
make down
# expands to: docker compose down -v
```

Note: `-v` deletes the Compose PostgreSQL/Redis volumes.

#### Python install and checks

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

Integration dependencies and tests:

```bash
docker compose up -d postgres redis
alembic -c backend/alembic.ini upgrade head
pytest -q -m integration backend/tests/integration
```

Make targets:

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

`make unit` currently expands to `pytest -m 'not integration and not chaos'`, which also includes tests marked `property`; see finding DEP-05.

#### Frontend

```bash
cd web
npm install
npm run dev
npm run lint   # actually: tsc --noEmit
npm run build
npm run start
```

There is no JavaScript/TypeScript unit/component/E2E test command configured.

#### Load test

```bash
./scripts/run-benchmark.sh
```

## Section 2: Baseline test/lint results ("before")

### Baseline provenance

GitHub Actions run `34223921288` cleanly checked out `master` at `9bf12168d30f9407fc7e88eb1c90c201d1f5c73a` with `actions/checkout` cleaning the workspace first. All five CI jobs concluded `success`. This is the closest reproducible clean baseline available in this session; it is **not** represented as a local shell run.

### Exact results

| Check | Exact command | Before result |
|---|---|---|
| Python install | `python -m pip install -e '.[dev]'` | Success on CPython 3.13.15 |
| Ruff | `ruff check .` | **PASS — 0 violations** (`All checks passed!`) |
| mypy | `mypy backend/app` | **PASS — 0 errors in 14 source files** |
| Unit tests | `pytest -q backend/tests/unit` | **8 passed, 0 failed, 0 errors** in 0.85s |
| Property tests | `pytest -q -m property backend/tests/property` | **1 passed, 0 failed, 0 errors** in 0.79s |
| Recovery-model tests | `pytest -q -m chaos backend/tests/chaos` | **2 passed, 0 failed, 0 errors** in 0.14s |
| DB migration | `alembic -c backend/alembic.ini upgrade head` | PASS against PostgreSQL 17 |
| Integration tests | `pytest -q -m integration backend/tests/integration` | **6 passed, 0 failed, 0 errors** in 1.87s |
| Python pytest total | Sum of the four non-overlapping CI pytest invocations above | **17 passed, 0 failed, 0 errors** |
| Web install/audit signal | `npm install` | Install succeeds, but npm reports **3 vulnerabilities: 1 critical, 2 high** |
| Web type check | `npm run lint` -> `tsc --noEmit` | **PASS — 0 TypeScript errors** |
| Web production build | `npm run build` | PASS, with **1 Autoprefixer compatibility warning** (`align-items:end`; recommends `flex-end`) |
| Docker images | `docker compose build api worker scheduler web` | PASS |
| Destructive recovery | `./scripts/chaos-compose.sh` | PASS; script exits successfully with `All Docker chaos/recovery scenarios passed.` |

### Dependency-audit baseline

- npm's clean install audited 28 packages and reported **1 Critical + 2 High vulnerabilities**. It explicitly warned that `next@15.5.2` contains a security vulnerability and referenced CVE-2025-66478.
- Current upstream security guidance (checked 2026-09-08) places Next.js 15.x in Maintenance LTS and the August 2026 security release directs 15.x users to `15.5.24`; `15.5.2` is therefore well behind the patched 15.5 line. Reference: `https://nextjs.org/blog` (August 25, 2026 security release) and `https://nextjs.org/blog/CVE-2025-66478`.
- Python has **no configured `pip-audit`, Safety, Dependabot gate, or equivalent dependency-vulnerability command in this repository's CI**. Because arbitrary local command execution was unavailable, no separate Python vulnerability count is claimed.

### Coverage baseline

No coverage package/configuration or coverage threshold is present. Therefore an exact line/branch coverage percentage — and exact "zero-coverage line" count — cannot be honestly reported. The test-gap findings below identify modules/features with no direct repository tests, while acknowledging that the Compose chaos harness indirectly exercises parts of the runtime.

## Section 3: Findings

### A. Correctness & distributed-systems behavior

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| COR-01 | `backend/app/engine.py` (`reap_expired_leases`) | When an expired lease consumes the final attempt, the task is set to `dead_letter` but the workflow is **not recomputed**. A workflow can therefore remain `running` forever even though one of its tasks is terminally dead-lettered. The normal `mark_task_failed()` dead-letter path does recompute workflow state, so the two terminal-failure paths are inconsistent. | High | Small |
| COR-02 | `backend/app/config.py`, `backend/app/worker.py` | `worker_id` defaults to the non-empty literal `worker-local`, so the worker's hostname/PID fallback is normally unreachable. Starting multiple workers without explicitly setting unique IDs makes them share the same DB worker row, Redis consumer name and lease-owner identity; one process can appear to own/renew/commit work attributed to another process using the same string. | High | Small |
| COR-03 | `backend/app/worker.py` (`renew_loop`, shutdown path) | Lease renewal stops as soon as global `self.stop` is set, but graceful shutdown still waits for active tasks. A draining long-running task can therefore lose its lease while still executing, allowing recovery/retry to overlap with the original execution. | High | Small |
| COR-04 | `backend/app/queue.py`, `backend/app/worker.py` | Redis reads only new consumer-group entries (`XREADGROUP ... '>'`) and implements no `XAUTOCLAIM`/`XCLAIM` path. If a consumer dies after delivery but before ACK, the old PEL entry is never reclaimed/ACKed. PostgreSQL lease recovery can create a new stream message, so work may recover, but the original pending entry leaks indefinitely and Redis-level at-least-once redelivery is incomplete. | High | Medium |
| COR-05 | `backend/app/queue.py`, `backend/app/worker.py` | Poison handling catches missing fields only. A fields-present but malformed `task_id` reaches `uuid.UUID(message.task_id)`, raises, and is neither poison-DLQed nor ACKed; combined with unobserved child-task exceptions it can remain stuck in PEL. | Medium | Small |
| COR-06 | `backend/app/engine.py` | When a prerequisite becomes `dead_letter`/failed, workflow state can become failed while downstream dependent tasks remain `pending` indefinitely. Those tasks never become an explicit terminal `skipped`/`cancelled` state, leaving internally nonterminal task state inside a terminal workflow and complicating reconstruction/retention. | Medium | Medium |
| COR-07 | `backend/app/worker.py` (`heartbeat_loop`) | A transient DB exception exits the heartbeat coroutine permanently; the main worker loop continues processing but the scheduler can mark the worker stale and heartbeat never self-recovers. | Medium | Small |
| COR-08 | `backend/app/worker.py` (`run`) | `process_message()` tasks are detached into a set and their done callback only discards them; exceptions are never retrieved/logged. Infrastructure/programming failures can become `Task exception was never retrieved` warnings with no structured recovery signal. | Medium | Small |
| COR-09 | `backend/app/queue.py` (`_TOKEN_BUCKET_LUA`, `wait_for_rate_token`) | Distributed rate limiting uses each worker's local wall clock (`time.time()`) as the authoritative timestamp. Clock skew between hosts can over- or under-refill the shared token bucket. | Medium | Medium |
| COR-10 | `backend/app/models.py`, `backend/app/engine.py` | `scheduled` is a valid runtime state but is represented as raw string literals and is missing from `TaskState`, an incomplete state-model refactor that weakens type/consistency checks. | Low | Small |
| COR-11 | `backend/app/engine.py` (`workflow_query`) | `workflow_query()` is declared `async` although it contains no await and only returns a SQLAlchemy `Select`; no use was found. This is dead/misleading API surface. | Low | Small |

### B. Outbox, resilience & observability

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| RES-01 | `backend/app/scheduler.py` (`OutboxPublisher.publish_batch`) | Publisher always selects the oldest unpublished 100 rows. Permanently malformed/unpublishable rows stay unpublished forever; once enough bad rows occupy the head of the queue, each pass can select the same failures and starve newer valid events. There is no retry backoff, `next_attempt_at`, quarantine/dead-outbox state or max-attempt policy. | High | Large |
| RES-02 | `backend/app/scheduler.py` | Both individual outbox publish failures and the outer scheduler loop catch broad `Exception` without logging or an error metric. Real bugs and prolonged infrastructure outages are intentionally retried but operationally invisible. | Medium | Small |
| RES-03 | `backend/app/scheduler.py` (`update_metrics`) | Any Redis exception while calculating queue depth is converted to depth `0`, so an outage can be graphed as an empty/healthy queue rather than unknown/unavailable. | Medium | Small |
| RES-04 | `backend/app/scheduler.py` (`publish_batch`) | The scheduler holds PostgreSQL row locks/transaction state while awaiting Redis network publication for up to a batch of 100 events. Redis latency/outage can prolong DB locks and couple queue health to DB contention. Safely restructuring this interacts with outbox delivery guarantees. | Medium | Large |

### C. Security

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| SEC-01 | `web/package.json`, web resolved dependency graph | `next` is pinned to vulnerable `15.5.2`. Clean CI `npm install` reports **3 vulnerabilities (1 critical, 2 high)** and explicitly warns about the Next.js security issue. Current upstream security release for the same 15.5 line is `15.5.24`, allowing a patch-line upgrade without a major-version migration. | Critical | Small |
| SEC-02 | `docker-compose.yml` | Dev services publish ports on all host interfaces by default: PostgreSQL uses known `workflow/workflow`, Redis has no password, Grafana is `admin/admin`, and API/metrics/OTLP are also host-published. On a laptop/server reachable from another network namespace/host, this exposes trusted local infrastructure unnecessarily. | High | Small |
| SEC-03 | `backend/app/main.py` | API has no authentication or authorization. Any network caller that can reach it can submit/list/read/cancel workflows and inspect worker/dead-letter state. A real fix requires an explicit identity/tenant/authorization model and changes the public API/security contract. | High | Large |
| SEC-04 | `backend/app/schemas.py`, `backend/app/main.py` | The unauthenticated submission endpoint accepts arbitrary nested JSON payloads and there is no explicit HTTP request/body-size budget. Field counts are bounded for tasks, but payload byte/depth size is not, enabling excessive memory/DB storage consumption. A limit would tighten the public input contract. | High | Medium |

No hardcoded production API keys/secrets were found in the reviewed source. The credentials above are clearly development defaults, but become a security problem because Compose publishes their services broadly.

CORS is **not** globally permissive: it allows only `http://localhost:3000`; `allow_headers=['*']` is not treated as a standalone finding because credentials are not enabled and the origin is restricted.

### D. Performance & resource management

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| PERF-01 | `backend/app/queue.py`, Redis Streams used by worker/scheduler | Every `XADD` is unbounded: task streams, `dwe:events`, poison DLQ and task DLQ have no `MAXLEN`/retention policy, and ACK does not delete stream entries. Long-lived deployments can grow Redis memory/disk without bound. Safe trimming must account for pending consumers and DLQ retention. | High | Large |
| PERF-02 | `backend/app/engine.py` (`republish_orphaned_queued`), `backend/app/scheduler.py` | Any queued task whose DB `updated_at` is older than roughly a lease duration is republished, without checking whether a valid Redis stream message is merely waiting in backlog/PEL. Under sustained queue delay this can repeatedly manufacture duplicates and amplify stream growth/consumer work. A safe fix needs explicit publication/recovery semantics. | High | Large |
| PERF-03 | `backend/app/engine.py` (`mark_task_succeeded`, `_lock_workflow_tasks`) | Every task success locks and loads **all** tasks in the workflow and scans them to find children/dependency readiness. Across a large DAG (up to 1000 tasks), execution trends toward O(N²) DB/data-processing work and increases lock contention. | Medium | Medium |
| PERF-04 | `backend/app/main.py` (`get_workflow`) | Workflow detail returns the entire ordered event timeline with no limit/cursor. High-retry/long-lived workflows can make a single polling response grow without bound and repeatedly query/serialize the whole history. | Medium | Small |
| PERF-05 | `backend/app/main.py` (`healthz`) | Every health request creates and closes a new Redis client rather than reusing an application-lifetime pool/client. | Low | Small |
| PERF-06 | `web/app/page.tsx` | UI polls global data every 2.5s and detail every 1.5s; the global polling effect also depends on `selected`, causing it to restart on every selection. This is acceptable for a demo but scales poorly with clients and adds avoidable requests. | Low | Medium |

The main task scheduling/reaping query predicates have supporting composite indexes (`state, lease_expires_at` and `state, available_at`), and workflow task/event foreign-key access is indexed; no obvious N+1 query was found in the API routes reviewed.

### E. Frontend correctness / UX behavior

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| WEB-01 | `web/app/page.tsx` | Changing `selected` does not clear the previous `detail`. Until the next request succeeds — and indefinitely if it fails — the page can keep displaying stale details from the previously selected workflow. | Medium | Small |
| WEB-02 | `web/app/page.tsx` | Both polling effects write to one `error` string, while a successful global refresh unconditionally clears it. A detail-fetch failure can therefore be masked by an unrelated successful workers/workflows refresh. | Low | Small |

No duplicated frontend routes or obviously non-functional controls were found. The UI is monitoring-only; the absence of action buttons is consistent with that stated role rather than a stub feature.

### F. Dependencies, build reproducibility & lint/tooling

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| DEP-01 | `web/package.json`, `web/Dockerfile`, `.github/workflows/ci.yml` | No `web/package-lock.json` is committed. CI and Docker use `npm install`, so transitive dependencies can change between identical source commits and `npm ci` cannot be used. This also makes vulnerability remediation less auditable. | Medium | Small |
| DEP-02 | `pyproject.toml`, `.github/workflows/ci.yml` | No Python dependency vulnerability scanner (`pip-audit` or equivalent) is installed/run, so Python CVEs are not gated despite dependency ranges resolving new versions on each build. | Medium | Small |
| DEP-03 | `web/package.json` | The script named `lint` is only `tsc --noEmit`; there is no ESLint/other source linter for React/TypeScript. Type correctness is checked, style/React lint rules are not. | Low | Small |
| DEP-04 | `web/app/globals.css` | Production build emits an Autoprefixer warning for `align-items:end`; use `flex-end` for broader support and a warning-free build. | Low | Small |
| DEP-05 | `Makefile`, `pyproject.toml` pytest markers | `make unit` runs `pytest -m 'not integration and not chaos'`, which includes tests marked `property`; CI treats unit and property suites separately. Local `make test` therefore conflates/duplicates the intended suite partition. | Low | Small |
| DEP-06 | `pyproject.toml`, `backend/app/engine.py` | Ruff suppresses `F401` for the entire `engine.py` file, currently hiding at least the unused `and_` import and potentially future unused imports. | Low | Small |

### G. Dead code / incomplete refactors

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| DEAD-01 | `backend/app/config.py`, `backend/app/scheduler.py` | `outbox_poll_seconds` is configurable but never used; scheduler sleeps only `scheduler_poll_seconds`. | Low | Small |
| DEAD-02 | `backend/app/engine.py` | `workflow_query()` appears unused and is incorrectly asynchronous for a pure query-builder helper. | Low | Small |
| DEAD-03 | `backend/app/models.py`, initial Alembic migration | `EffectRecord`/`effect_records` exists in the durable schema but no runtime engine/worker path uses it. It is an incomplete/dead runtime feature; removing it would require a schema migration, while making it authoritative would require a deliberate side-effect/idempotency design. | Low | Large |

Repository-wide searches found **no `TODO` or `FIXME` comments** and no duplicate FastAPI route definitions. The deliberate `noop`/`sleep`/`flaky` handlers and simulator/test data are clearly demo/test mechanisms rather than fake production integrations being presented as real ones.

### H. Test coverage gaps

| ID | File path(s) | Description | Severity | Effort |
|---|---|---|---|---|
| TEST-01 | `backend/app/main.py` | No direct FastAPI/httpx route tests exist for request validation, 404s, idempotency header handling, cancellation route semantics, worker/DLQ endpoints, health behavior, CORS or response serialization. | Medium | Medium |
| TEST-02 | `backend/app/queue.py`, `backend/app/worker.py`, `backend/app/scheduler.py` | No focused tests cover PEL reclaim, malformed poison messages, Lua rate/concurrency guards, worker graceful shutdown, handler timeout/unknown handler, lease-lost behavior, heartbeat failure recovery, outbox partial/permanent failure, or metrics-on-Redis-failure. Chaos tests exercise some processes indirectly but not these edge contracts. | Medium | Medium |
| TEST-03 | `web/` | No frontend unit/component/E2E test framework or tests are configured. Selection/polling/stale-state behavior is therefore unprotected. | Medium | Medium |
| TEST-04 | `pyproject.toml`, `.github/workflows/ci.yml` | No line/branch coverage collection or threshold exists, so regressions can reduce exercised code without CI visibility. | Medium | Small |
| TEST-05 | `backend/alembic/`, `.github/workflows/ci.yml` | CI tests upgrade-to-head on a fresh DB but does not test migration downgrade/upgrade round trips or model-vs-migration drift. | Low | Medium |

## Section 4: Total counts

**41 findings total:**

- **Critical: 1**
- **High: 10**
- **Medium: 17**
- **Low: 13**

The most urgent safe fixes are the vulnerable Next.js dependency, unsafe default Compose port exposure, the expired-lease workflow-state bug, worker identity collision, graceful-shutdown lease renewal, and Redis pending-entry reclaim. The largest deferred design risks are API authorization, unbounded stream retention, orphan-republish semantics, permanent outbox poison handling, and DB-lock/outbox coupling.