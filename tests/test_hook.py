import io
import json
import os
import subprocess
import sys

import pytest

from kiro_otel_hook import hook, telemetry
from kiro_otel_hook.hook import cli, main


def _run_with_stdin(monkeypatch, payload):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload) if payload is not None else ""))


def test_main_noops_without_otlp_endpoint(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    _run_with_stdin(monkeypatch, {"hook_event_name": "SessionStart"})

    main()  # must not raise


def test_main_survives_invalid_json(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))

    main()  # must not raise, must not exit non-zero


def test_main_survives_empty_stdin(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    _run_with_stdin(monkeypatch, None)

    main()  # must not raise


@pytest.mark.parametrize("payload", [[], ["a"], "foo", 123, None])
def test_non_object_json_becomes_unknown_event(monkeypatch, payload):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    seen = []
    monkeypatch.setattr(telemetry, "emit", lambda event, config: seen.append(event))

    main()

    assert [e.event_name for e in seen] == ["unknown"]


def test_main_survives_unreachable_collector(monkeypatch):
    # Port 1 is a reserved/unassigned port that should refuse connections
    # immediately rather than hanging for the flush timeout.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    _run_with_stdin(monkeypatch, {"hook_event_name": "PostToolUse", "tool_name": "fs_write"})

    main()  # must not raise even though export fails


def test_cli_exits_zero_even_if_main_crashes_outside_its_try_blocks(monkeypatch):
    def boom():
        raise RuntimeError("config exploded")

    monkeypatch.setattr(hook, "load_config", boom)
    with pytest.raises(SystemExit) as exc:
        cli()
    assert exc.value.code == 0


def _run_module(stdin, env_overrides):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "KIRO_"))}
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "kiro_otel_hook"], input=stdin, env=env, capture_output=True, text=True, timeout=30
    )


def test_module_entry_point_exits_zero_on_garbage():
    result = _run_module("[[[", {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1"})
    assert result.returncode == 0


def test_disabled_path_never_loads_the_otel_sdk():
    env = {k: v for k, v in os.environ.items() if k != "OTEL_EXPORTER_OTLP_ENDPOINT"}
    code = (
        "import sys; from kiro_otel_hook.hook import main; main(); "
        "print('opentelemetry.sdk.trace' in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", code], input="{}", env=env, capture_output=True, text=True)
    assert result.stdout.strip() == "False"
