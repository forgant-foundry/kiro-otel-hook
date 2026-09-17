"""Kiro CLI hook that pushes agent metrics to OpenTelemetry via MLflow tracing."""

__all__ = ["main"]

from .hook import main
