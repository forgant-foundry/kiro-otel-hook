"""Exercises all three signals through in-memory exporters, so an
OpenTelemetry SDK change that breaks export fails CI instead of silently
dropping telemetry in production."""

import pytest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from kiro_otel_hook import telemetry
from kiro_otel_hook.config import Config
from kiro_otel_hook.payload import parse_hook_event

GITLAB_ENV = {"GITLAB_CI": "true", "CI_PIPELINE_ID": "42", "CI_COMMIT_SHA": "deadbeef", "GITLAB_USER_LOGIN": "dev"}


def _config(detail="metadata", **overrides):
    return Config(
        otlp_endpoint="http://unused",
        otlp_protocol="http/protobuf",
        service_name="kiro-test",
        experiment_name="exp",
        log_level="INFO",
        genai_detail=detail,
        **overrides,
    )


def _event():
    return parse_hook_event(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "cwd": "/repo",
            "tool_name": "fs_write",
            "duration_ms": 12,
            "tool_input": {"content": "SECRET=hunter2"},
        }
    )


def _run(detail, monkeypatch, span_exporter=None, **config_overrides):
    for key in list(GITLAB_ENV) + ["CI_JOB_ID"]:
        monkeypatch.delenv(key, raising=False)
    for key, value in GITLAB_ENV.items():
        monkeypatch.setenv(key, value)
    spans = span_exporter or InMemorySpanExporter()
    metrics = InMemoryMetricReader()
    logs = InMemoryLogRecordExporter()
    # InMemoryMetricReader is pull-based; grab the data before shutdown clears it.
    collected = {}
    original_shutdown = metrics.shutdown

    def _capture_then_shutdown(*args, **kwargs):
        collected["data"] = metrics.get_metrics_data()
        return original_shutdown(*args, **kwargs)

    metrics.shutdown = _capture_then_shutdown
    telemetry.emit(_event(), _config(detail, **config_overrides), telemetry.Exporters(span=spans, metric_reader=metrics, log=logs))
    return spans, collected.get("data"), logs


def _log_record(readable):
    return getattr(readable, "log_record", readable)


def test_metadata_level_emits_identifiers_but_no_content(monkeypatch):
    spans, metric_data, logs = _run("metadata", monkeypatch)

    (span,) = spans.get_finished_spans()
    attrs = dict(span.attributes)
    assert span.name == "PostToolUse"
    assert attrs["kiro.session_id"] == "s1"
    assert attrs["kiro.tool_name"] == "fs_write"
    assert attrs["kiro.reported_duration_ms"] == 12.0
    assert attrs["mlflow.spanType"] == "TOOL"
    assert attrs["gen_ai.tool.name"] == "fs_write"
    assert not any(k.startswith("kiro.raw.") for k in attrs)
    assert "mlflow.spanInputs" not in attrs
    assert "hunter2" not in repr(attrs)

    (record,) = logs.get_finished_logs()
    log = _log_record(record)
    assert log.body == "kiro hook PostToolUse (fs_write)"
    assert "hunter2" not in repr(dict(log.attributes))
    assert log.trace_id == span.context.trace_id

    (point,) = [
        p
        for rm in metric_data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
        if m.name == telemetry.DURATION_METRIC
        for p in m.data.data_points
    ]
    assert point.count == 1
    # Cardinality discipline: no per-event labels on the metric.
    assert dict(point.attributes) == {"span.type": "TOOL", "span.status": "OK", "kiro.experiment": "exp"}


def test_ci_attributes_on_resource_and_span(monkeypatch):
    spans, metric_data, _ = _run("off", monkeypatch)
    (span,) = spans.get_finished_spans()
    assert span.attributes["gitlab.pipeline.id"] == "42"
    assert span.resource.attributes["gitlab.commit.sha"] == "deadbeef"
    assert span.resource.attributes["service.name"] == "kiro-test"
    # Metrics share the resource, which is how they correlate with traces.
    assert metric_data.resource_metrics[0].resource.attributes["gitlab.pipeline.id"] == "42"


def test_off_level_drops_genai_framing(monkeypatch):
    spans, _, _ = _run("off", monkeypatch)
    (span,) = spans.get_finished_spans()
    assert span.attributes["kiro.tool_name"] == "fs_write"
    assert not any(k.startswith(("mlflow.", "gen_ai.", "kiro.raw.")) for k in span.attributes)


def test_full_level_captures_content(monkeypatch):
    spans, _, logs = _run("full", monkeypatch)
    (span,) = spans.get_finished_spans()
    assert "hunter2" in span.attributes["kiro.raw.tool_input"]
    assert "hunter2" in span.attributes["mlflow.spanInputs"]
    assert span.attributes["mlflow.spanOutputs"] == '{"status": "ok"}'
    (record,) = logs.get_finished_logs()
    assert "kiro.raw.tool_input" in _log_record(record).attributes


class _FailingSpanExporter(InMemorySpanExporter):
    def export(self, spans):
        super().export(spans)
        return SpanExportResult.FAILURE


def test_unreachable_collector_skips_remaining_exports(monkeypatch):
    _, metric_data, logs = _run("metadata", monkeypatch, span_exporter=_FailingSpanExporter())
    assert logs.get_finished_logs() == ()
    assert metric_data is None  # meter provider never shut down -> no final export


@pytest.mark.parametrize("protocol", ["http/protobuf", "grpc"])
def test_otlp_exporters_build_for_each_protocol(protocol):
    exporters = telemetry._otlp_exporters(protocol)
    assert exporters.span is not None and exporters.log is not None
    exporters.metric_reader.shutdown()


def test_mlflow_recognized_identifiers_and_trace_tags(monkeypatch):
    spans, _, logs = _run("metadata", monkeypatch)
    (span,) = spans.get_finished_spans()
    attrs = span.attributes
    # Semantic-convention ids MLflow maps to the trace's session and user.
    assert attrs["session.id"] == "s1"
    assert attrs["user.id"] == "dev"
    # Root-span attributes MLflow turns into trace tags.
    assert attrs["mlflow.traceTag.kiro.tool_name"] == "fs_write"
    assert attrs["mlflow.traceTag.kiro.hook_event_name"] == "PostToolUse"
    assert attrs["mlflow.traceTag.gitlab.pipeline.id"] == "42"
    (record,) = logs.get_finished_logs()
    log_attrs = _log_record(record).attributes
    assert log_attrs["session.id"] == "s1"
    assert not any(k.startswith("mlflow.traceTag.") for k in log_attrs)


def test_off_level_keeps_identifiers_but_no_trace_tags(monkeypatch):
    spans, _, _ = _run("off", monkeypatch)
    (span,) = spans.get_finished_spans()
    assert span.attributes["session.id"] == "s1"
    assert not any(k.startswith("mlflow.") for k in span.attributes)


def test_signals_can_be_disabled(monkeypatch):
    spans, metric_data, logs = _run(
        "metadata", monkeypatch, metrics_enabled=False, logs_enabled=False
    )
    assert len(spans.get_finished_spans()) == 1
    assert logs.get_finished_logs() == ()
    assert metric_data is None or not metric_data.resource_metrics

    spans, metric_data, logs = _run("metadata", monkeypatch, traces_enabled=False)
    assert spans.get_finished_spans() == ()
    assert len(logs.get_finished_logs()) == 1
