#!/usr/bin/env bash
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
COMPOSE="docker compose"

on_error() {
  echo 'Chaos scenario failed; recent service logs:' >&2
  $COMPOSE logs --no-color --tail=120 api worker scheduler postgres redis >&2 || true
}
trap on_error ERR

json_field() {
  local field="$1"
  python -c "import json,sys; print(json.load(sys.stdin)['$field'])"
}

wait_health() {
  for _ in $(seq 1 90); do
    if curl -fsS "$API/healthz" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo 'API did not become healthy' >&2
  return 1
}

submit() {
  local key="$1"
  local body="$2"
  curl -fsS -X POST "$API/v1/workflows" \
    -H 'Content-Type: application/json' \
    -H "Idempotency-Key: $key" \
    -d "$body"
}

state() {
  curl -fsS "$API/v1/workflows/$1" | json_field state
}

wait_state() {
  local id="$1" expected="$2" timeout="$3"
  for _ in $(seq 1 "$timeout"); do
    local actual
    actual="$(state "$id" || true)"
    if [[ "$actual" == "$expected" ]]; then return 0; fi
    if [[ "$actual" == 'failed' || "$actual" == 'cancelled' ]]; then
      echo "workflow $id reached unexpected terminal state $actual" >&2
      return 1
    fi
    sleep 1
  done
  echo "workflow $id did not reach $expected" >&2
  return 1
}

wait_task_running() {
  local id="$1"
  for _ in $(seq 1 30); do
    if curl -fsS "$API/v1/workflows/$id" | python -c "import json,sys; d=json.load(sys.stdin); raise SystemExit(0 if any(t['state']=='running' for t in d['tasks']) else 1)"; then return 0; fi
    sleep 1
  done
  return 1
}

$COMPOSE up -d --build
wait_health

# 1) Kill a worker during execution. The expired lease must be retried.
BODY='{"name":"kill-worker","tasks":[{"key":"slow","kind":"sleep","payload":{"seconds":8},"timeout_seconds":20,"retry":{"max_attempts":4,"base_delay_seconds":0.1,"max_delay_seconds":0.2}}]}'
ID="$(submit "chaos-kill-$(date +%s%N)" "$BODY" | json_field id)"
wait_task_running "$ID"
$COMPOSE kill worker
$COMPOSE up -d worker
wait_state "$ID" succeeded 45

# 2) Duplicate a queue message. The durable lease/state transition rejects duplicate execution.
BODY='{"name":"duplicate-message","tasks":[{"key":"one","kind":"sleep","payload":{"seconds":2}}]}'
ID="$(submit "chaos-dup-msg-$(date +%s%N)" "$BODY" | json_field id)"
TASK_ID="$(curl -fsS "$API/v1/workflows/$ID" | python -c "import json,sys; print(json.load(sys.stdin)['tasks'][0]['id'])")"
PARTITION="$(python -c "import hashlib; s='$TASK_ID'; print(int.from_bytes(hashlib.blake2b(s.encode(),digest_size=8).digest(),'big') % 4)")"
$COMPOSE exec -T redis redis-cli XADD "dwe:q:default:$PARTITION" '*' task_id "$TASK_ID" workflow_id "$ID" queue default traceparent '' tracestate '' >/dev/null
wait_state "$ID" succeeded 30

# 3) Delay worker heartbeat/lease renewal long enough to force lease recovery.
BODY='{"name":"heartbeat-delay","tasks":[{"key":"slow","kind":"sleep","payload":{"seconds":15},"timeout_seconds":30,"retry":{"max_attempts":4,"base_delay_seconds":0.1,"max_delay_seconds":0.2}}]}'
ID="$(submit "chaos-heartbeat-$(date +%s%N)" "$BODY" | json_field id)"
wait_task_running "$ID"
$COMPOSE pause worker
sleep 12
$COMPOSE unpause worker
wait_state "$ID" succeeded 45

# 4) Restart Redis while durable work exists. PostgreSQL/outbox must preserve progress.
BODY='{"name":"redis-restart","tasks":[{"key":"a","kind":"sleep","payload":{"seconds":3}},{"key":"b","kind":"noop","depends_on":["a"]}]}'
ID="$(submit "chaos-redis-$(date +%s%N)" "$BODY" | json_field id)"
$COMPOSE restart redis
$COMPOSE up -d worker scheduler
wait_health
wait_state "$ID" succeeded 45

# 5) Restart the API. Workers and scheduler continue from PostgreSQL.
BODY='{"name":"api-restart","tasks":[{"key":"a","kind":"sleep","payload":{"seconds":4}},{"key":"b","kind":"noop","depends_on":["a"]}]}'
ID="$(submit "chaos-api-$(date +%s%N)" "$BODY" | json_field id)"
$COMPOSE restart api
wait_health
wait_state "$ID" succeeded 30

# 6) Retry a failed external-operation analogue.
BODY='{"name":"retry-external","tasks":[{"key":"provider_call","kind":"flaky","payload":{"fail_until_attempt":1},"retry":{"max_attempts":3,"base_delay_seconds":0.1,"max_delay_seconds":0.2}}]}'
ID="$(submit "chaos-flaky-$(date +%s%N)" "$BODY" | json_field id)"
wait_state "$ID" succeeded 30
ATTEMPT="$(curl -fsS "$API/v1/workflows/$ID" | python -c "import json,sys; print(json.load(sys.stdin)['tasks'][0]['attempt'])")"
[[ "$ATTEMPT" -ge 2 ]]

# 7) Duplicate client submissions with one idempotency key return one workflow.
KEY="chaos-client-$(date +%s%N)"
BODY='{"name":"duplicate-client","tasks":[{"key":"one","kind":"noop"}]}'
FIRST="$(submit "$KEY" "$BODY" | json_field id)"
SECOND="$(submit "$KEY" "$BODY" | json_field id)"
[[ "$FIRST" == "$SECOND" ]]
wait_state "$FIRST" succeeded 30

echo 'All Docker chaos/recovery scenarios passed.'
if [[ "${KEEP_STACK:-0}" != '1' ]]; then $COMPOSE down -v; fi
