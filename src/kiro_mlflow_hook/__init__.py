"""Kiro CLI hook that pushes agent telemetry (traces, metrics, logs) to OpenTelemetry."""

__all__ = ["cli", "main"]

from .hook import cli, main
