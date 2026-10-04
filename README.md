# kiro-mlflow-hook

A [kiro-cli](https://kiro.dev/docs/cli/) hook that pushes agent telemetry
(session starts, tool calls, agent stops) to OpenTelemetry as traces,
metrics, and logs. It speaks plain OTLP, so it works with any
OTLP-compatible backend. The traces are also formatted so
[MLflow's trace UI](https://mlflow.org/docs/latest/genai/tracing/opentelemetry/)
renders them natively. Point Kiro at this repo and it has everything it
needs (`.kiro/steering/`) to set itself up for you.

## Requirements

- **kiro-cli 3.0 or newer.** On kiro-cli 2.13 through 2.x, the V3 engine
  is available behind a flag: start sessions with `kiro-cli chat --v3`.
  The default 2.x engine does **not** read `.kiro/hooks/*.json` files, so
  the hook never fires there.
- The hook file uses the V3 standalone hook schema
  (`{"version": "v1", "hooks": [...]}`) with the canonical V3 triggers
  `SessionStart`, `PostToolUse` (matcher `.*`), and `Stop`.
- Python 3.10+.

## How it works

```
kiro-cli hook event (stdin JSON)
        │
        ▼
kiro-mlflow-hook                     # this repo; OpenTelemetry SDK, no MLflow SDK
        │
        │  1 span + 1 log record + 1 histogram point per hook firing
        ▼
OTLP (http/protobuf or grpc)
        │
        ▼
OpenTelemetry Collector  ── traces ──▶  MLflow (/v1/traces), Tempo, Jaeger, ...
        │
        ├── metrics ──▶  Prometheus, ...
        │
        └── logs ─────▶  Loki, ...
```

The hook is backend-agnostic. The collector, not the hook, decides where each
signal lands, so you swap backends by editing `docker/otel-collector-config.yaml`
(or your production collector config) only. MLflow is one optional trace sink.

- **Traces**: one span per hook firing, named after the hook event, with
  `kiro.*` attributes (event name, session id, cwd, tool name, reported
  duration). At the default detail level the span also carries the
  `mlflow.spanType` and `gen_ai.*` attributes that make MLflow and other
  GenAI-aware UIs render it as a tool call.
- **Metrics**: a `kiro.hook.duration` histogram (milliseconds), labeled only
  with `span.type`, `span.status`, and `kiro.experiment`. Its `_count`
  series is the number of hook firings. Per-event labels like tool name stay
  off the metric; see "Design constraints".
- **Logs**: one `INFO` log record per hook firing with a content-free body
  such as `kiro hook PostToolUse (fs_write)`. It carries the same attributes
  as the span plus the span's trace id, so traces, metrics, and logs
  correlate on the same keys.
- **CI correlation**: inside GitLab CI (`GITLAB_CI` set), GitLab's predefined
  variables become `gitlab.*` attributes: pipeline, job, commit, merge
  request, user, and runner. They are set on the OpenTelemetry *resource*,
  so all three signals carry them, and on the span and log record. Outside
  CI nothing is added. See `src/kiro_mlflow_hook/ci.py` for the full map.

See `.kiro/steering/mlflow-otel-hook.md` for the version of these
instructions written for Kiro itself.

## Data handling

The kiro-cli hook payload can contain prompt text, tool inputs and outputs,
file contents, and command output. That can carry **source code, secrets, or
PII** into your telemetry backend, and with it the backend inherits the data
sensitivity of the workload being observed. `KIRO_OTEL_GENAI_DETAIL`
controls how much leaves the process:

| Level | What is exported |
|---|---|
| `off` | Hook event metadata only (`kiro.*` identifiers and timing) and CI context. No GenAI/MLflow framing, no content. |
| `metadata` (**default**) | Everything in `off`, plus GenAI/MLflow span framing (span type, tool name). Still **no** prompt, response, or tool content. |
| `full` | Everything in `metadata`, plus payload content: every other payload field as `kiro.raw.<key>` (truncated to 512 chars), and `mlflow.spanInputs`/`mlflow.spanOutputs`. Opt-in only. |

Unrecognized values fall back to `metadata` with a warning, so a typo never
turns content capture on. Keep the default on shared machines and CI runners.

## Setup

Install the package so the `kiro-mlflow-hook` command is on `PATH`, then
register the hook either per repo or for your whole user account.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
source .env
```

**Per repo.** The hook file `.kiro/hooks/mlflow-otel-metrics.json` is already
active for this repo. kiro-cli picks up `.kiro/hooks/` automatically, with no
registration step. To add it to another project:

```bash
scripts/install.sh /path/to/target-repo
```

**User level, every workspace.** kiro-cli V3 also reads `~/.kiro/hooks/*.json`
and fires those hooks in every workspace. This is the easiest way to
instrument every agent run on a machine or CI runner:

```bash
scripts/install.sh --user
```

Use one or the other for a given workspace. If both are present, every event
is recorded twice.

The hook runs `kiro-mlflow-hook` by name, so the Python environment it was
installed into must be on `PATH` when kiro-cli starts.

### Running it on a GitLab runner

Bake the hook into the runner image and configure it through job or runner
variables:

```dockerfile
RUN pip install /path/to/kiro-mlflow-hook \
 && mkdir -p /root/.kiro/hooks \
 && cp /path/to/kiro-mlflow-hook/.kiro/hooks/mlflow-otel-metrics.json /root/.kiro/hooks/
```

```yaml
variables:
  OTEL_EXPORTER_OTLP_ENDPOINT: "https://otel-collector.example.internal:4318"
  OTEL_SERVICE_NAME: "kiro-cli"
  KIRO_OTEL_GENAI_DETAIL: "metadata"
```

Every signal then carries `gitlab.pipeline.id`, `gitlab.job.id`,
`gitlab.commit.sha`, `gitlab.user.login`, and the rest automatically.

## Validating the pipeline (docker compose)

```bash
docker compose up -d --build
```

This starts:

| Service          | Port | Purpose                                             |
|-------------------|------|------------------------------------------------------|
| `mlflow`          | 5001 | Tracking server with OTLP trace ingestion (`/v1/traces`) |
| `otel-collector`  | 4317/4318 | Receives OTLP from the hook, fans out to MLflow, Prometheus, and Loki |
| `prometheus`      | 9090 | Scrapes the collector's Prometheus exporter          |
| `loki`            | 3100 | Log store with native OTLP ingestion                 |

Wait for `docker compose ps` to show `mlflow` healthy, then fire the hook by
hand exactly like kiro-cli would:

```bash
echo '{"hook_event_name":"PostToolUse","tool_name":"fs_write","session_id":"demo","cwd":"'"$PWD"'","duration_ms":123}' \
  | kiro-mlflow-hook
```

It should exit 0 with no output. Telemetry failures are logged to stderr,
never raised; see "Design constraints".

Then check the signals arrived:

- **Traces**: open http://localhost:5001, select the "Default" experiment,
  open the Traces tab. You should see a `PostToolUse` trace with a
  `kiro.tool_name=fs_write` span attribute.
- **Metrics**: `curl -s http://localhost:9090/api/v1/query --data-urlencode 'query=kiro_hook_duration_milliseconds_count'`
  (Prometheus normalizes the OTLP histogram name) or browse
  http://localhost:9090/graph.
- **Logs**: `curl -sG http://localhost:3100/loki/api/v1/query_range --data-urlencode 'query={service_name="kiro-cli"}'`

`scripts/smoke_test.sh` automates the above end to end. It needs the MLflow
client to query traces back out: `pip install -e ".[smoke]"`.

Tear down with `docker compose down -v`.

## Configuration

All configuration is environment variables (see `.env.example`):

| Variable | Purpose |
|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Base OTLP endpoint. Each signal's path (`/v1/traces`, `/v1/metrics`, `/v1/logs`) is appended automatically. Unset disables the hook entirely. Per-signal `OTEL_EXPORTER_OTLP_<SIGNAL>_ENDPOINT` overrides also work. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` (default) or `grpc`. gRPC needs `pip install "kiro-mlflow-hook[grpc]"`. |
| `OTEL_EXPORTER_OTLP_TIMEOUT` | Export timeout in **seconds**. Defaults to `2` so an unreachable collector can't stall a tool call. |
| `OTEL_SERVICE_NAME` | `service.name` resource attribute on all signals. |
| `OTEL_RESOURCE_ATTRIBUTES` | Extra resource attributes, merged as usual. |
| `KIRO_OTEL_GENAI_DETAIL` | `off`, `metadata` (default), or `full`. See "Data handling". |
| `KIRO_MLFLOW_EXPERIMENT_NAME` | Human-readable label added as `kiro.experiment`. The actual MLflow experiment traces land in is decided by the collector's `x-mlflow-experiment-id` header; see `docker/otel-collector-config.yaml`. |
| `KIRO_MLFLOW_HOOK_LOG_LEVEL` | Hook's own stderr log verbosity. |

## Design constraints

- **The hook must always exit 0.** A crashing hook pollutes the kiro-cli
  session and looks like a real error. This is structural: the
  `kiro-mlflow-hook` command and `python -m kiro_mlflow_hook` both enter
  through `hook.cli()`, which wraps everything and forces exit code 0.
  Each failure stage (bad JSON, unexpected payload shape, unreachable
  collector) is also caught and logged to stderr.
- **The kiro-cli payload schema is not fully specified** across triggers and
  CLI versions. Only `hook_event_name`, `cwd`, and `session_id` are
  documented as stable. `payload.py` extracts known fields defensively and
  captures everything else generically. Valid JSON that is not an object is
  treated as an empty payload, producing a minimal `unknown` event.
- **The hook drives the OpenTelemetry SDK directly, not MLflow's tracing
  SDK.** An earlier version exported through MLflow's SDK, which forced a
  flush through a private MLflow API and hit a crash in
  `mlflow.update_current_trace(tags=...)` at span end. Owning the providers
  makes flushing a public call, and MLflow compatibility comes from plain
  `mlflow.*` span attributes.
- **Export is synchronous and bounded.** Each hook firing is a fresh Python
  process, so there is nothing for batching to amortize. Spans and log
  records use the OpenTelemetry *Simple* processors, metrics are pushed by
  an explicit `force_flush()`, and every export is bounded by
  `OTEL_EXPORTER_OTLP_TIMEOUT`. If the span export fails, metrics and logs
  are skipped rather than waiting out the timeout twice more.
- **Metric labels stay low-cardinality.** Tool name, session id, cwd, and CI
  ids live on spans, log records, and the resource, never as metric labels.
  Adding them as labels would make every session a new Prometheus series.
- **The disabled path is cheap.** With no endpoint set, the hook exits before
  importing the OpenTelemetry SDK. It fires on every tool call, so keep the
  `telemetry` import behind that check.

## Development

```bash
pip install -e ".[dev]"
pytest
```
