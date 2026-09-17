#!/usr/bin/env bash
# End-to-end validation against the docker-compose stack: fires a fake
# PostToolUse hook payload through the real hook entry point, then checks
# that a trace landed in MLflow and a metric landed in Prometheus.
#
# Uses the mlflow Python client (already a dependency) rather than hand-built
# REST calls to search traces -- MLflow's trace search API has moved between
# versions (v2 -> v3) and the exact request/response shape isn't worth
# re-deriving here when the client already handles it.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4318}"
export OTEL_EXPORTER_OTLP_PROTOCOL="${OTEL_EXPORTER_OTLP_PROTOCOL:-http/protobuf}"
export OTEL_SERVICE_NAME="${OTEL_SERVICE_NAME:-kiro-cli-smoke-test}"
export MLFLOW_DISABLE_AGENT_HINT="${MLFLOW_DISABLE_AGENT_HINT:-1}"

mlflow_url="${MLFLOW_URL:-http://localhost:5001}"
prometheus_url="${PROMETHEUS_URL:-http://localhost:9090}"
marker="smoke-$(date +%s)"

echo "==> waiting for mlflow at $mlflow_url"
for _ in $(seq 1 30); do
    if curl -sf "$mlflow_url/health" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done
curl -sf "$mlflow_url/health" >/dev/null || { echo "mlflow never became healthy" >&2; exit 1; }

echo "==> firing a fake PostToolUse hook event (session_id=$marker)"
echo "{\"hook_event_name\":\"PostToolUse\",\"tool_name\":\"fs_write\",\"session_id\":\"$marker\",\"cwd\":\"$repo_root\",\"duration_ms\":123}" \
    | python3 -m kiro_mlflow_hook

echo "==> waiting for the collector to forward the trace to mlflow"
MARKER="$marker" MLFLOW_URL="$mlflow_url" python3 <<'EOF'
import os
import sys
import time

import mlflow

marker = os.environ["MARKER"]
mlflow.set_tracking_uri(os.environ["MLFLOW_URL"])

for _ in range(15):
    traces = mlflow.search_traces(locations=["0"], max_results=20)
    for _, row in traces.iterrows():
        trace = mlflow.get_trace(row.trace_id)
        for span in trace.data.spans:
            if span.attributes.get("kiro.session_id") == marker:
                print(f"OK: trace {row.trace_id} found in mlflow, tagged kiro.tool_name={span.attributes.get('kiro.tool_name')}")
                sys.exit(0)
    time.sleep(2)

print(f"FAIL: no trace tagged kiro.session_id={marker} found in mlflow experiment 0", file=sys.stderr)
sys.exit(1)
EOF

echo "==> checking prometheus scraped the duration histogram"
metric_resp="$(curl -sf "$prometheus_url/api/v1/query" --data-urlencode 'query=mlflow_trace_span_duration_milliseconds_count' || true)"
if echo "$metric_resp" | grep -q '"resultType":"vector"' && echo "$metric_resp" | grep -q '"value"'; then
    echo "OK: mlflow_trace_span_duration_milliseconds_count present in prometheus"
else
    echo "WARN: metric not visible in prometheus yet (scrape_interval is 5s in docker/prometheus.yml -- try again in a few seconds)" >&2
fi

echo "==> smoke test complete"
