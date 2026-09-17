"""Environment-driven configuration for the hook process.

All settings come from the environment so the same hook binary behaves
differently per kiro-cli agent config or shell profile without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    otlp_endpoint: str | None
    otlp_protocol: str
    service_name: str
    experiment_name: str
    log_level: str

    @property
    def otel_enabled(self) -> bool:
        return bool(self.otlp_endpoint)


def load_config() -> Config:
    # The OTLP exporters' default timeout (10s, per the OTel spec -- note the
    # env var's value is in *seconds* despite the lack of a "_MS" suffix)
    # bounds the exporter's whole retry-with-backoff budget, not a single
    # attempt. An unreachable collector can otherwise stall a hook invocation
    # for 10+ seconds -- very noticeable on every tool call. Set a short
    # budget unless the caller already configured one explicitly.
    os.environ.setdefault("OTEL_EXPORTER_OTLP_TIMEOUT", "2")

    # MLflow prints an unrelated "load this skill before writing tracing
    # code" hint on every span, aimed at coding agents authoring new
    # instrumentation. It's just stderr noise for this fixed, already-written
    # hook; each kiro-cli tool call would otherwise print it repeatedly.
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

    return Config(
        otlp_endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        otlp_protocol=os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"),
        service_name=os.environ.get("OTEL_SERVICE_NAME", "kiro-cli"),
        experiment_name=os.environ.get("KIRO_MLFLOW_EXPERIMENT_NAME", "kiro-cli"),
        log_level=os.environ.get("KIRO_MLFLOW_HOOK_LOG_LEVEL", "INFO"),
    )
