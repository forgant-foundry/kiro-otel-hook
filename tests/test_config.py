import pytest

from kiro_mlflow_hook.config import load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("KIRO_OTEL_GENAI_DETAIL", raising=False)


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
