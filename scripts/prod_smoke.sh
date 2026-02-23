#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-}"
if [[ -z "${BASE_URL}" ]]; then
  echo "usage: $0 <base-url>"
  echo "example: $0 https://aex-kernel.fly.dev"
  exit 1
fi

echo "[1/4] health"
curl -fsS "${BASE_URL%/}/health" | jq .

echo "[2/4] ready"
curl -fsS "${BASE_URL%/}/ready" | jq '.ready,.checks'

echo "[3/4] alerts"
curl -fsS "${BASE_URL%/}/admin/alerts" | jq '.summary'

echo "[4/4] dashboard payload"
curl -fsS "${BASE_URL%/}/admin/dashboard/data?limit=20" | jq '.summary,.alert_summary,.dashboard_ok'

echo "prod smoke: PASS"
