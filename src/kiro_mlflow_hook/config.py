"""Environment-driven configuration for the hook process.

All settings come from the environment so the same hook binary behaves
differently per kiro-cli agent config, shell profile, or CI runner without
code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# How much of the agent's activity is captured. See README "Data handling".
GENAI_DETAIL_OFF = "off"  # hook event metadata only; no GenAI framing, no content
GENAI_DETAIL_METADATA = "metadata"  # + GenAI/MLflow span framing; still no content
GENAI_DETAIL_FULL = "full"  # + payload content (kiro.raw.*, span inputs/outputs)
GENAI_DETAIL_LEVELS = (GENAI_DETAIL_OFF, GENAI_DETAIL_METADATA, GENAI_DETAIL_FULL)
DEFAULT_GENAI_DETAIL = GENAI_DETAIL_METADATA


@dataclass(frozen=True)
class Config:
    otlp_endpoint: str | None
    otlp_protocol: str
    service_name: str
    experiment_name: str
    log_level: str
    genai_detail: str = DEFAULT_GENAI_DETAIL
    # Set when KIRO_OTEL_GENAI_DETAIL held something unrecognized, so the
    # caller can warn once logging is configured.
    genai_detail_invalid: str | None = None

    @property
    def otel_enabled(self) -> bool:
        return bool(self.otlp_endpoint)

    @property
    def capture_content(self) -> bool:
        return self.genai_detail == GENAI_DETAIL_FULL


def _parse_genai_detail(raw: str | None) -> tuple[str, str | None]:
    """Return (level, invalid_value). Unknown values fall back to the safe
    default rather than guessing -- content capture must be an explicit,
    correctly spelled opt-in."""
    if raw is None or not raw.strip():
        return DEFAULT_GENAI_DETAIL, None
    value = raw.strip().lower()
    if value in GENAI_DETAIL_LEVELS:
        return value, None
    return DEFAULT_GENAI_DETAIL, raw


def load_config() -> Config:
    # The OTLP exporters' default timeout (10s, per the OTel spec -- note the
    # env var's value is in *seconds* despite the lack of a "_MS" suffix)
    # bounds the exporter's whole retry-with-backoff budget, not a single
    # attempt. An unreachable collector can otherwise stall a hook invocation
    # for 10+ seconds -- very noticeable on every tool call. Set a short
    # budget unless the caller already configured one explicitly.
    os.environ.setdefault("OTEL_EXPORTER_OTLP_TIMEOUT", "2")

    genai_detail, genai_detail_invalid = _parse_genai_detail(os.environ.get("KIRO_OTEL_GENAI_DETAIL"))

    return Config(
        otlp_endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        otlp_protocol=os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"),
        service_name=os.environ.get("OTEL_SERVICE_NAME", "kiro-cli"),
        experiment_name=os.environ.get("KIRO_MLFLOW_EXPERIMENT_NAME", "kiro-cli"),
        log_level=os.environ.get("KIRO_MLFLOW_HOOK_LOG_LEVEL", "INFO"),
        genai_detail=genai_detail,
        genai_detail_invalid=genai_detail_invalid,
    )
