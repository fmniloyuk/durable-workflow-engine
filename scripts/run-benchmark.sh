#!/usr/bin/env bash
set -euo pipefail

command -v k6 >/dev/null 2>&1 || { echo 'k6 is required: https://grafana.com/docs/k6/latest/set-up/install-k6/' >&2; exit 1; }
API_URL="${API_URL:-http://localhost:8000}"
curl -fsS "$API_URL/healthz" >/dev/null || { echo "API is not healthy at $API_URL" >&2; exit 1; }
mkdir -p load/results
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="load/results/k6-${STAMP}.json"

echo "Running k6 against $API_URL; results -> $OUT"
API_URL="$API_URL" SUMMARY_PATH="$OUT" k6 run load/k6-workflows.js

echo "Benchmark complete. Commit the result only with hardware/software context."
