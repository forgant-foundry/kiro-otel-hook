---
inclusion: always
---

# kiro-mlflow-hook: what this repo is and how to set it up

This repository is both the source for, and a working example of, a kiro-cli
hook that pushes agent telemetry (tool calls, agent-stop events) to
OpenTelemetry, using MLflow's tracing SDK as the trace pipeline. If a user
points you at this repo and asks you to "set it up" or "get it running,"
follow the steps below directly instead of asking for a spec.

## What's here

- `src/kiro_mlflow_hook/` -- the hook implementation (Python). Entry point is
  `kiro_mlflow_hook.hook:main`, invoked as `python3 -m kiro_mlflow_hook`.
- `.kiro/hooks/mlflow-otel-metrics.json` -- registers the hook against
  `AgentSpawn`, `PostToolUse`, and `AgentStop` triggers. This file is already
  active in this repo (hooks in `.kiro/hooks/` activate automatically, no
  registration step needed).
- `docker-compose.yml` + `docker/` -- a local validation stack: an MLflow
  tracking server (with OTLP trace ingestion), an OpenTelemetry Collector,
  and Prometheus. This is how you prove the pipeline works end to end
  without needing real observability infrastructure.
- `README.md` -- human-facing setup instructions; keep this file and the
  README in sync if you change the setup flow.

## Setup steps (do these in order)

1. Create a venv and install the package in editable mode:
   `python3 -m venv .venv && source .venv/bin/activate && pip install -e .`
2. Copy `.env.example` to `.env` and `source .env` (or otherwise export those
   vars) before running `kiro-cli` in this project, so the hook knows where
   to send telemetry.
3. Bring up the validation stack: `docker compose up -d --build`. Wait for
   `docker compose ps` to show `mlflow` healthy.
4. Confirm the hook runs cleanly on its own before trusting it inside a real
   session: `echo '{"hook_event_name":"AgentSpawn","session_id":"smoke","cwd":"'"$PWD"'"}' | python3 -m kiro_mlflow_hook`
   It must exit 0 even if it can't reach the collector -- this hook is
   non-blocking by design and must never fail a kiro-cli session.
5. Validate signals arrived (or just run `scripts/smoke_test.sh`, which
   automates this step):
   - Traces: open http://localhost:5001, select the "Default" experiment,
     check the Traces tab.
   - Metrics: `curl -s http://localhost:9090/api/v1/query --data-urlencode 'query=mlflow_trace_span_duration_milliseconds_count'`
     or browse http://localhost:9090.
6. Run `pytest` to check the unit tests still pass after any change here.

## Adopting this hook in a different repo

To reuse this hook in another project (rather than working in this repo
directly): copy `.kiro/hooks/mlflow-otel-metrics.json` and the
`kiro_mlflow_hook` package into the target repo (or `pip install` it from
this repo), then repeat steps 1-2 above there. `scripts/install.sh` in this
repo automates that copy step; see `#install-elsewhere` steering for details
on demand.

## Design constraints to preserve

- The hook must always exit 0. `PostToolUse` and `AgentStop` are
  non-blocking, but a crashing hook still pollutes kiro-cli's session output
  and can mask real errors -- all telemetry failures are caught and logged
  to stderr, never raised.
- The hook payload schema from kiro-cli is not fully specified across
  triggers/versions. Payload parsing (`payload.py`) must stay defensive:
  known fields are extracted explicitly, everything else is captured
  generically rather than causing a `KeyError`.
- Telemetry export happens in a short-lived, one-shot process (a new Python
  interpreter per hook invocation), so exporters must be explicitly flushed
  before exit -- don't rely on background batch export threads surviving
  process teardown.
- Per-event metadata (tool name, session id, cwd, ...) is attached via
  `mlflow.start_span(attributes=...)` / `span.set_inputs(...)`, never via
  `mlflow.update_current_trace(tags=...)`. The latter crashes span end in
  this OTLP-only export path on mlflow 3.16.1 (it tries to write trace tags
  into the OTel span's attributes after OTel has already frozen them at
  `on_end()`). See the NOTE at the top of `telemetry.py::emit`.
- The dockerized MLflow server's `--allowed-hosts` must include both the
  collector's in-network hostname (`mlflow:5000`) and whatever host:port a
  browser/client uses from outside the compose network (`localhost:5001`
  by default) -- MLflow's host-header security middleware 403s any request
  whose `Host` header isn't on that list, and setting `--allowed-hosts`
  replaces its default allowlist rather than extending it.
