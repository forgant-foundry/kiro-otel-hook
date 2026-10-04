import pytest

from kiro_otel_hook.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "KIRO_OTEL_GENAI_DETAIL",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "OTEL_TRACES_EXPORTER",
        "OTEL_METRICS_EXPORTER",
        "OTEL_LOGS_EXPORTER",
    ):
        monkeypatch.delenv(var, raising=False)


def test_genai_detail_defaults_to_metadata():
    config = load_config()
    assert config.genai_detail == "metadata"
    assert not config.capture_content
    assert config.genai_detail_invalid is None


@pytest.mark.parametrize("value, expected", [("off", "off"), ("FULL", "full"), (" metadata ", "metadata")])
def test_genai_detail_accepts_known_levels(monkeypatch, value, expected):
    monkeypatch.setenv("KIRO_OTEL_GENAI_DETAIL", value)
    assert load_config().genai_detail == expected


@pytest.mark.parametrize("value", ["fulll", "true", "1", "all"])
def test_unknown_genai_detail_falls_back_to_safe_default(monkeypatch, value):
    # A typo must never silently turn content capture on.
    monkeypatch.setenv("KIRO_OTEL_GENAI_DETAIL", value)
    config = load_config()
    assert config.genai_detail == "metadata"
    assert not config.capture_content
    assert config.genai_detail_invalid == value


def test_disabled_without_any_endpoint():
    assert not load_config().otel_enabled


def test_per_signal_endpoint_enables_the_hook(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://mlflow:5000/v1/traces")
    assert load_config().otel_enabled


def test_standard_exporter_none_disables_a_signal(monkeypatch):
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", " None ")
    config = load_config()
    assert config.traces_enabled
    assert not config.metrics_enabled
    assert not config.logs_enabled
