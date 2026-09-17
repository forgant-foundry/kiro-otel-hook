"""Emit one MLflow trace (and, via MLflow's OTLP span processor, an
OpenTelemetry duration metric) for a single kiro-cli hook invocation.

Both signals are driven purely by the standard OTEL_EXPORTER_OTLP_* env vars:
setting an endpoint makes mlflow.start_span export spans as OTLP traces, and
additionally setting a metrics endpoint makes MLflow's span processor also
emit an OTLP histogram (`mlflow.trace.span.duration`) per span, labeled with
span_type/span_status/experiment_id. See MLflow's tracing/processor/otel.py
and otel_metrics_mixin.py for the exact mechanics this relies on.

Per-event details (hook name, tool name, session id, ...) are attached as
span attributes/inputs, not `mlflow.update_current_trace(tags=...)` -- see
the NOTE in emit() below for why.

This process is short-lived: one Python interpreter per hook firing, not a
long-running service. MLflow's OTLP trace export batches spans on
OpenTelemetry's BatchSpanProcessor (default export interval: 5s) and the
metrics reader batches on a 60s interval, so without an explicit flush here,
telemetry from a one-shot process would very often never leave the process
before it exits. Both are force_flush()'d with a short bounded timeout below.
"""

from __future__ import annotations

import logging

import mlflow
from opentelemetry import metrics as otel_metrics

from .config import Config
from .payload import HookEvent

_logger = logging.getLogger(__name__)

_FLUSH_TIMEOUT_MS = 3_000


def emit(event: HookEvent, config: Config) -> None:
    attrs: dict[str, str] = {
        "kiro.hook_event_name": event.event_name,
        "kiro.experiment": config.experiment_name,
    }
    if event.session_id:
        attrs["kiro.session_id"] = event.session_id
    if event.cwd:
        attrs["kiro.cwd"] = event.cwd
    if event.tool_name:
        attrs["kiro.tool_name"] = event.tool_name
    if event.duration_ms is not None:
        attrs["kiro.reported_duration_ms"] = str(event.duration_ms)
    attrs.update(event.attributes)

    span_type = "TOOL" if event.tool_name else "UNKNOWN"
    # NOTE: metadata is passed via start_span(attributes=...) / set_inputs(),
    # deliberately not mlflow.update_current_trace(tags=...). In this
    # OTLP-only export path (mlflow==3.16.1), update_current_trace crashes
    # span end: MLflow tries to bake trace tags into the OTel span's
    # attributes at on_end() time, but OTel has already frozen them once the
    # span ends (mlflow/tracing/processor/otel.py:79 -> TypeError from
    # BoundedAttributes.__setitem__). attributes= at span creation carries
    # the same information without touching that frozen-attributes path.
    with mlflow.start_span(name=event.event_name, span_type=span_type, attributes=attrs) as span:
        span.set_inputs(attrs)
        span.set_outputs({"status": "ok"})

    _flush()


def _flush() -> None:
    # mlflow.flush_trace_async_logging() only drains MLflow's own tracking-server
    # queue, not the OTLP BatchSpanProcessor -- so the OTLP path needs its own
    # explicit flush. `_get_span_processor` is a private MLflow API; if it moves
    # in a future MLflow release this degrades to "telemetry occasionally lost
    # to batching," not a hook crash, which is the safe failure mode here.
    try:
        from mlflow.tracing.provider import _get_span_processor

        processor = _get_span_processor()
        if processor is not None and hasattr(processor, "force_flush"):
            processor.force_flush(timeout_millis=_FLUSH_TIMEOUT_MS)
    except Exception:
        _logger.debug("Failed to flush MLflow span processor", exc_info=True)

    try:
        otel_metrics.get_meter_provider().force_flush(timeout_millis=_FLUSH_TIMEOUT_MS)
    except Exception:
        _logger.debug("Failed to flush OpenTelemetry meter provider", exc_info=True)
