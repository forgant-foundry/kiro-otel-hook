import io
import json
import sys

import pytest

from kiro_mlflow_hook.hook import main


def _run_with_stdin(monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload) if payload is not None else ""))


def test_main_noops_without_otlp_endpoint(monkeypatch, capsys):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    _run_with_stdin(monkeypatch, {"hook_event_name": "AgentSpawn"})

    main()  # must not raise


def test_main_survives_invalid_json(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))

    main()  # must not raise, must not exit non-zero


def test_main_survives_empty_stdin(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    _run_with_stdin(monkeypatch, None)

    main()  # must not raise


def test_main_survives_unreachable_collector(monkeypatch):
    # Port 1 is a reserved/unassigned port that should refuse connections
    # immediately rather than hanging for the flush timeout.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    _run_with_stdin(monkeypatch, {"hook_event_name": "PostToolUse", "tool_name": "fs_write"})

    main()  # must not raise even though export fails
