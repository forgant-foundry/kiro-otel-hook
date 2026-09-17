# kiro-mlflow-hook

A [kiro-cli](https://kiro.dev/docs/cli/) hook that pushes agent telemetry
(tool calls, agent-stop events) to OpenTelemetry, using [MLflow's tracing
SDK](https://mlflow.org/docs/latest/genai/tracing/opentelemetry/) as the
export path. Point Kiro at this repo and it has everything it needs
(`.kiro/steering/`) to set itself up for you.

## How it works

```
kiro-cli hook event (stdin JSON)
        │
        ▼
python3 -m kiro_mlflow_hook          # this repo
        │
        │  mlflow.start_span(...) + mlflow.update_current_trace(tags=...)
        ▼
OTLP/HTTP  (traces + a per-span duration histogram metric)
        │
        ▼
OpenTelemetry Collector  ── traces ──▶  MLflow Tracking Server (/v1/traces)
        │
        └── metrics ──▶  Prometheus
```

- **Traces**: each hook firing becomes one MLflow trace (`mlflow.start_span`),
  with span attributes/inputs for the hook event name, session id, cwd, tool
  name, and anything else present in the payload. MLflow exports it as a
  real OTLP span -- no MLflow tracking server is required for export, only
  for ingestion.
- **Metrics**: MLflow's own OTLP span processor emits an OpenTelemetry
  histogram (`mlflow.trace.span.duration`) per span when a metrics endpoint
  is configured, labeled with span type/status/experiment id. No separate
  metrics instrumentation code needed -- this is MLflow's built-in behavior.
  (Per-event labels like tool name live on the trace/span, not the metric --
  see "Design constraints" for why.)
- Both signals go to a single OpenTelemetry Collector endpoint; the collector
  (not this hook) is responsible for routing traces to MLflow and metrics to
  Prometheus/Grafana/Datadog/whatever you actually use.

See `.kiro/steering/mlflow-otel-hook.md` for the version of these
instructions written for Kiro itself.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
source .env
```

The hook itself (`.kiro/hooks/mlflow-otel-metrics.json`) is already active
for this repo -- kiro-cli picks up `.kiro/hooks/` automatically, no
registration step needed. It fires on `AgentSpawn`, `PostToolUse`, and
`AgentStop`.

To use it in a different project, see
`.kiro/steering/install-elsewhere.md` / `scripts/install.sh`.

## Validating the pipeline (docker compose)

```bash
docker compose up -d --build
```

This starts:

| Service          | Port | Purpose                                             |
|-------------------|------|------------------------------------------------------|
| `mlflow`          | 5001 | Tracking server with OTLP trace ingestion (`/v1/traces`) |
| `otel-collector`   | 4317/4318 | Receives OTLP from the hook, fans out to MLflow + Prometheus |
| `prometheus`       | 9090 | Scrapes the collector's Prometheus exporter          |

Wait for `docker compose ps` to show `mlflow` healthy, then fire the hook by
hand exactly like kiro-cli would:

```bash
echo '{"hook_event_name":"PostToolUse","tool_name":"fs_write","session_id":"demo","cwd":"'"$PWD"'","duration_ms":123}' \
  | python3 -m kiro_mlflow_hook
```

It should exit 0 with no output (telemetry failures are logged to stderr,
never raised -- see "Design constraints" below).

Then check the signals arrived:

- **Traces**: open http://localhost:5001, select the "Default" experiment,
  open the Traces tab -- you should see a `PostToolUse` trace with a
  `kiro.tool_name=fs_write` span attribute.
- **Metrics**: `curl -s http://localhost:9090/api/v1/query --data-urlencode 'query=mlflow_trace_span_duration_milliseconds_count'`
  (Prometheus normalizes the OTLP histogram name) or browse
  http://localhost:9090/graph.

`scripts/smoke_test.sh` automates the above end to end.

Tear down with `docker compose down -v`.

## Configuration

All configuration is environment variables (see `.env.example`):

| Variable | Purpose |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Base OTLP endpoint (traces get `/v1/traces`, metrics `/v1/metrics` appended automatically). Unset disables the hook entirely. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` or `grpc`. |
| `OTEL_SERVICE_NAME` | `service.name` resource attribute on emitted telemetry. |
| `KIRO_MLFLOW_EXPERIMENT_NAME` | Human-readable label added as a trace tag (`kiro.experiment`). The actual MLflow experiment traces land in is decided by the collector's `x-mlflow-experiment-id` header -- see `docker/otel-collector-config.yaml`. |
| `KIRO_MLFLOW_HOOK_LOG_LEVEL` | Hook's own stderr log verbosity. |

## Design constraints

- **The hook must always exit 0.** `PostToolUse` and `AgentStop` are
  non-blocking kiro-cli triggers, but a crashing hook still pollutes session
  output. Every failure path (bad JSON, unreachable collector, unexpected
  payload shape) is caught and logged to stderr, never raised.
- **The kiro-cli payload schema is not fully specified** across triggers and
  CLI versions -- only `hook_event_name`, `cwd`, and `session_id` are
  documented as stable. `payload.py` extracts known fields defensively and
  captures everything else generically, rather than assuming a shape.
- **Per-event metadata goes on the span/trace, not via `mlflow.update_current_trace(tags=...)`.**
  In this OTLP-only export path, that call crashes span end in mlflow 3.16.1
  (MLflow tries to bake trace tags into the OTel span's attributes at
  `on_end()`, after OTel has already frozen them) -- see the NOTE in
  `telemetry.py`. `start_span(attributes=...)` carries the same information
  without hitting that path.
- **Export is explicitly flushed.** Each hook invocation is a fresh Python
  process; MLflow's OTLP span processor and the OTel metrics reader both
  batch asynchronously by default, so this hook force-flushes both (with a
  short bounded timeout, see `OTEL_EXPORTER_OTLP_TIMEOUT` in `config.py`)
  before exiting, and never lets a slow/unreachable collector stall a hook
  for more than ~2-3 seconds.

## Development

```bash
pip install -e ".[dev]"
pytest
```
