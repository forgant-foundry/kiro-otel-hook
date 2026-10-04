---
inclusion: always
---

# kiro-mlflow-hook: what this repo is and how to set it up

This repository is both the source for, and a working example of, a kiro-cli
hook that pushes agent telemetry (session starts, tool calls, agent stops) to
OpenTelemetry as traces, metrics, and logs. It drives the OpenTelemetry SDK
directly and is backend-agnostic; spans carry `mlflow.*` attributes so
MLflow's trace UI renders them, but MLflow is just one optional trace sink
behind the collector. If a user points you at this repo and asks you to
"set it up" or "get it running," follow the steps below directly instead of
asking for a spec.

## Requirements

kiro-cli 3.0+, or 2.13+ started with `kiro-cli chat --v3`. The default 2.x
engine does not read `.kiro/hooks/*.json`, so the hook never fires there.

## What's here

- `src/kiro_mlflow_hook/` -- the hook implementation (Python). The console
  entry point is `kiro_mlflow_hook.hook:cli` (`kiro-mlflow-hook` on PATH, or
  `python3 -m kiro_mlflow_hook`). `cli()` wraps `main()` and always exits 0.
- `.kiro/hooks/mlflow-otel-metrics.json` -- registers the hook against the
  canonical V3 triggers `SessionStart`, `PostToolUse` (matcher `.*`), and
  `Stop`. Already active in this repo (hooks in `.kiro/hooks/` activate
  automatically, no registration step needed).
- `docker-compose.yml` + `docker/` -- a local validation stack: an MLflow
  tracking server (with OTLP trace ingestion), an OpenTelemetry Collector,
  Prometheus, and Loki. This is how you prove the pipeline works end to end
  without needing real observability infrastructure.
- `README.md` -- human-facing setup instructions; keep this file and the
  README in sync if you change the setup flow.

## Setup steps (do these in order)

1. Create a venv and install the package in editable mode:
   `python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev,smoke]"`
   Keep this venv activated when starting kiro-cli so `kiro-mlflow-hook` is
   on PATH.
2. Copy `.env.example` to `.env` and `source .env` (or otherwise export those
   vars) before running `kiro-cli` in this project, so the hook knows where
   to send telemetry. Leave `KIRO_OTEL_GENAI_DETAIL` at `metadata` unless
   the user explicitly asks to capture prompt/tool content.
3. Bring up the validation stack: `docker compose up -d --build`. Wait for
   `docker compose ps` to show `mlflow` healthy.
4. Confirm the hook runs cleanly on its own before trusting it inside a real
   session: `echo '{"hook_event_name":"SessionStart","session_id":"smoke","cwd":"'"$PWD"'"}' | kiro-mlflow-hook`
   It must exit 0 even if it can't reach the collector -- this hook is
   non-blocking by design and must never fail a kiro-cli session.
5. Validate signals arrived (or just run `scripts/smoke_test.sh`, which
   automates this step):
   - Traces: open http://localhost:5001, select the "Default" experiment,
     check the Traces tab.
   - Metrics: `curl -s http://localhost:9090/api/v1/query --data-urlencode 'query=kiro_hook_duration_milliseconds_count'`
   - Logs: `curl -sG http://localhost:3100/loki/api/v1/query_range --data-urlencode 'query={service_name="kiro-cli"}'`
6. Run `pytest` to check the unit tests still pass after any change here.

## Adopting this hook elsewhere

`scripts/install.sh /path/to/repo` installs it per repo;
`scripts/install.sh --user` installs it into `~/.kiro/hooks/` so it fires in
every workspace (the right choice for CI runners). See `#install-elsewhere`
steering for details on demand.

## Design constraints to preserve

- The hook must always exit 0, structurally: every entry point goes through
  `hook.cli()`. Don't add entry points that bypass it.
- The hook payload schema from kiro-cli is not fully specified across
  triggers/versions. Payload parsing (`payload.py`) must stay defensive:
  known fields are extracted explicitly, everything else is captured
  generically, and non-object JSON is treated as an empty payload.
- Content capture is gated by `KIRO_OTEL_GENAI_DETAIL` (`off` / `metadata`
  default / `full`). Payload content (`kiro.raw.*`, `mlflow.spanInputs`)
  must only be exported at `full`. Unknown values fall back to `metadata`.
- Export is synchronous and bounded: Simple span/log processors, explicit
  `force_flush()` on providers this hook owns. Don't reintroduce batching or
  any dependency on a backend SDK's private flush internals.
- Metric labels stay low-cardinality (`span.type`, `span.status`,
  `kiro.experiment`). Tool name, session id, cwd, and CI ids go on spans,
  log records, and the resource -- never metric labels.
- CI identifiers (`gitlab.*`, see `ci.py`) go on the OTel Resource so all
  three signals correlate, and are emitted at every detail level.
- The `telemetry` import in `hook.py` must stay after the disabled check, so
  the no-endpoint path never loads the OTel SDK.
- `mlflow.*` span attributes are sent as plain values (not JSON-encoded):
  MLflow's OTLP ingestion JSON-encodes every attribute it receives.
- MLflow support stays attribute-based, with no MLflow library: `session.id`
  and `user.id` (semantic conventions MLflow maps to the trace's session and
  user) and `mlflow.traceTag.<key>` root-span attributes (become trace
  tags). For collector-less MLflow export, see README "Using with MLflow".
- The dockerized MLflow server's `--allowed-hosts` must include both the
  collector's in-network hostname (`mlflow:5000`) and whatever host:port a
  browser/client uses from outside the compose network (`localhost:5001`
  by default) -- MLflow's host-header security middleware 403s any request
  whose `Host` header isn't on that list, and setting `--allowed-hosts`
  replaces its default allowlist rather than extending it.
