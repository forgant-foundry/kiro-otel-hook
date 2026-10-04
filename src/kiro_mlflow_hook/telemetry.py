"""Emit one OTLP span, one OTLP log record, and one histogram data point for
a single kiro-cli hook invocation, using the OpenTelemetry SDK directly.

The hook is backend-agnostic: it speaks plain OTLP to whatever endpoint
OTEL_EXPORTER_OTLP_ENDPOINT names, and the collector decides where traces,
metrics, and logs land. MLflow is one optional trace sink -- the
`mlflow.spanType` / `mlflow.spanInputs` / `mlflow.spanOutputs` span
attributes below are what MLflow's OTLP ingestion uses to render the span
nicely, and they are just span attributes; no MLflow SDK is involved.

Why not the MLflow tracing SDK (which an earlier version used)? Its OTLP
path batches spans on a BatchSpanProcessor it owns, and the only way to
flush that from a one-shot process was a private MLflow API
(`mlflow.tracing.provider._get_span_processor`). If that moved, the hook
would keep exiting 0 while silently exporting nothing. Owning the providers
here makes flushing a public, first-class call. It also sidesteps MLflow's
`update_current_trace(tags=...)` crash at span end, which wrote trace tags
into the OTel span's already-frozen attributes.

This process is short-lived: one interpreter per hook firing. There is no
steady state for batching to amortize over, so spans and log records use
the *Simple* (synchronous) processors and are exported as they end. Metrics
are collected by a reader whose periodic interval is effectively disabled,
and exported by an explicit, bounded force_flush(). Every export is bounded
by OTEL_EXPORTER_OTLP_TIMEOUT (default 2s, see config.py) and every flush by
_FLUSH_TIMEOUT_MS, so an unreachable collector can't hang the hook.

Data sensitivity is governed by KIRO_OTEL_GENAI_DETAIL (see config.py):
payload content only leaves the process at `full`.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry._logs import LogRecord, SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import LogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode

from .ci import ci_attributes
from .config import GENAI_DETAIL_OFF, Config
from .payload import HookEvent

_logger = logging.getLogger(__name__)

_FLUSH_TIMEOUT_MS = 3_000
# The reader only exports on force_flush(); never on a timer.
_METRIC_EXPORT_INTERVAL_MS = 24 * 60 * 60 * 1000
_SCOPE = "kiro_mlflow_hook"

DURATION_METRIC = "kiro.hook.duration"

# Attributes mirrored as `mlflow.traceTag.<key>`, which MLflow's OTLP
# ingestion turns into searchable trace tags. Plus every gitlab.* attribute.
_TRACE_TAG_KEYS = ("kiro.hook_event_name", "kiro.tool_name", "kiro.experiment")
_TRACE_TAG_PREFIX = "mlflow.traceTag."

AttrValue = str | bool | int | float


@dataclass(frozen=True)
class Exporters:
    """The three export sinks. Tests inject in-memory ones; production uses
    OTLP exporters configured entirely from the standard OTEL_* env vars."""

    span: SpanExporter
    metric_reader: MetricReader
    log: LogRecordExporter


class _ResultTrackingSpanExporter(SpanExporter):
    """Records whether the span export succeeded. All three signals normally
    share one collector, so if the span couldn't get through there is no
    point paying the export timeout twice more for metrics and logs."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner
        self.failed = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result = self._inner.export(spans)
        except Exception:
            self.failed = True
            raise
        if result is not SpanExportResult.SUCCESS:
            self.failed = True
        return result

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._inner.force_flush(timeout_millis)


def _otlp_exporters(protocol: str) -> Exporters:
    if protocol == "grpc":
        try:
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        except ImportError:
            _logger.warning(
                "OTEL_EXPORTER_OTLP_PROTOCOL=grpc but the gRPC exporter isn't installed "
                "(pip install 'kiro-mlflow-hook[grpc]'); falling back to http/protobuf"
            )
            return _otlp_exporters("http/protobuf")
    else:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    # No arguments: endpoint, headers, timeout, and per-signal overrides all
    # come from the standard OTEL_EXPORTER_OTLP_* env vars.
    return Exporters(
        span=OTLPSpanExporter(),
        metric_reader=PeriodicExportingMetricReader(
            OTLPMetricExporter(), export_interval_millis=_METRIC_EXPORT_INTERVAL_MS
        ),
        log=OTLPLogExporter(),
    )


def build_resource(config: Config, ci_attrs: Mapping[str, str]) -> Resource:
    # Resource.create() also merges OTEL_RESOURCE_ATTRIBUTES. CI identifiers
    # live here, not only on the span, because the Resource is the one thing
    # all three providers share -- it's how metrics and logs correlate too.
    return Resource.create({"service.name": config.service_name, **ci_attrs})


def _span_type(event: HookEvent) -> str:
    return "TOOL" if event.tool_name else "UNKNOWN"


def build_attributes(
    event: HookEvent, config: Config, ci_attrs: Mapping[str, str]
) -> dict[str, AttrValue]:
    """Span (and log record) attributes for one hook event, gated by
    KIRO_OTEL_GENAI_DETAIL:

    - every level: kiro.* identifiers/timing and gitlab.* CI context
    - metadata+:   GenAI / MLflow span framing (span type, tool name)
    - full:        payload content (kiro.raw.*, mlflow.spanInputs/Outputs)
    """
    attrs: dict[str, AttrValue] = {
        "kiro.hook_event_name": event.event_name,
        "kiro.experiment": config.experiment_name,
        "kiro.genai_detail": config.genai_detail,
    }
    if event.session_id:
        attrs["kiro.session_id"] = event.session_id
    if event.cwd:
        attrs["kiro.cwd"] = event.cwd
    if event.tool_name:
        attrs["kiro.tool_name"] = event.tool_name
    if event.duration_ms is not None:
        attrs["kiro.reported_duration_ms"] = event.duration_ms
    attrs.update(ci_attrs)

    # OpenTelemetry semantic-convention identifiers. MLflow's OTLP ingestion
    # also maps these onto the trace: session.id groups traces in its
    # Sessions view, and user.id sets the trace's user.
    if event.session_id:
        attrs["session.id"] = event.session_id
    if user := ci_attrs.get("gitlab.user.login"):
        attrs["user.id"] = user

    if config.genai_detail == GENAI_DETAIL_OFF:
        return attrs

    # Plain (not JSON-encoded) values: MLflow's OTLP ingestion JSON-encodes
    # every attribute it receives, the same way its own SDK stores them.
    attrs["mlflow.spanType"] = _span_type(event)
    if event.tool_name:
        attrs["gen_ai.operation.name"] = "execute_tool"
        attrs["gen_ai.tool.name"] = event.tool_name
    for key in (*_TRACE_TAG_KEYS, *ci_attrs):
        if key in attrs:
            attrs[_TRACE_TAG_PREFIX + key] = str(attrs[key])

    if config.capture_content:
        # kiro.raw.* can carry prompt text, tool input/output, file contents,
        # and command output -- i.e. source code, secrets, or PII.
        attrs.update(event.attributes)
        inputs = {k: v for k, v in attrs.items() if k.startswith("kiro.")}
        attrs["mlflow.spanInputs"] = json.dumps(inputs, default=str)
        attrs["mlflow.spanOutputs"] = json.dumps({"status": "ok"})

    return attrs


def _log_body(event: HookEvent) -> str:
    # Content-free by design: event name and tool name only.
    return f"kiro hook {event.event_name}" + (f" ({event.tool_name})" if event.tool_name else "")


def emit(event: HookEvent, config: Config, exporters: Exporters | None = None) -> None:
    ci_attrs = ci_attributes(os.environ)
    attrs = build_attributes(event, config, ci_attrs)
    resource = build_resource(config, ci_attrs)
    exporters = exporters or _otlp_exporters(config.otlp_protocol)

    span_exporter = _ResultTrackingSpanExporter(exporters.span)
    # shutdown_on_exit=False: providers are shut down explicitly below. The
    # default atexit hook would otherwise re-export metrics at interpreter
    # teardown -- a second, unbounded-by-us network call per hook firing.
    # A signal set to `none` via the standard OTEL_<SIGNAL>_EXPORTER env var
    # gets a provider with nothing attached, so it records but never exports.
    tracer_provider = TracerProvider(resource=resource, shutdown_on_exit=False)
    if config.traces_enabled:
        tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[exporters.metric_reader] if config.metrics_enabled else [],
        shutdown_on_exit=False,
    )
    logger_provider = LoggerProvider(resource=resource, shutdown_on_exit=False)
    if config.logs_enabled:
        logger_provider.add_log_record_processor(SimpleLogRecordProcessor(exporters.log))

    try:
        duration = meter_provider.get_meter(_SCOPE).create_histogram(
            DURATION_METRIC,
            unit="ms",
            description="Time spent handling one kiro-cli hook event; _count is hook firings.",
        )
        tracer = tracer_provider.get_tracer(_SCOPE)

        start = time.perf_counter()
        # SimpleSpanProcessor exports synchronously when this block exits.
        with tracer.start_as_current_span(event.event_name, attributes=attrs) as span:
            span.set_status(Status(StatusCode.OK))
            span_context = trace.set_span_in_context(span)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Metric labels stay low-cardinality on purpose: tool name, session
        # id, cwd, and CI ids live on the span/log/resource, never here.
        duration.record(
            elapsed_ms,
            {
                "span.type": _span_type(event),
                "span.status": "OK",
                "kiro.experiment": config.experiment_name,
            },
        )

        if span_exporter.failed:
            _logger.warning("Span export failed; skipping metric and log export (collector unreachable?)")
            return

        if config.logs_enabled:
            _emit_log_record(logger_provider, event, attrs, span_context)
        _bounded("meter provider", lambda: meter_provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MS))
        _bounded("tracer provider", lambda: tracer_provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MS))
        _bounded("logger provider", lambda: logger_provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MS))
    finally:
        _bounded("tracer provider shutdown", tracer_provider.shutdown)
        _bounded("logger provider shutdown", logger_provider.shutdown)
        # MeterProvider.shutdown() performs one last collect-and-export, so
        # skip it when the collector already proved unreachable; its reader
        # thread is a daemon and dies with the process.
        if not span_exporter.failed:
            _bounded(
                "meter provider shutdown",
                lambda: meter_provider.shutdown(timeout_millis=_FLUSH_TIMEOUT_MS),
            )


def _emit_log_record(
    logger_provider: LoggerProvider,
    event: HookEvent,
    attrs: Mapping[str, AttrValue],
    context: object,
) -> None:
    # Isolated so a logs-SDK problem (the OTel Python logs API still lives
    # under `_logs`) can never take traces or metrics down with it.
    try:
        logger_provider.get_logger(_SCOPE).emit(
            LogRecord(
                timestamp=time.time_ns(),
                context=context,
                severity_number=SeverityNumber.INFO,
                severity_text="INFO",
                body=_log_body(event),
                # Trace tags are an MLflow trace concept; don't repeat them on logs.
                attributes={k: v for k, v in attrs.items() if not k.startswith(_TRACE_TAG_PREFIX)},
            )
        )
    except Exception:
        _logger.debug("Failed to emit OTLP log record", exc_info=True)


def _bounded(what: str, fn) -> None:
    try:
        fn()
    except Exception:
        _logger.debug("Failed to flush/shut down %s", what, exc_info=True)
