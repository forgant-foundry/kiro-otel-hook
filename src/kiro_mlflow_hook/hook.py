"""Entry point invoked by kiro-cli as a hook command.

kiro-cli runs this as `python3 -m kiro_mlflow_hook`, piping the hook's JSON
session context to stdin. PostToolUse and AgentStop hooks are non-blocking
in kiro-cli, but a non-zero exit or stack trace still shows up in the
session and can be confused for a real error -- so every failure mode here
is caught and logged to stderr, and the process always exits 0.
"""

from __future__ import annotations

import json
import logging
import sys

from .config import load_config
from .payload import parse_hook_event


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="kiro-mlflow-hook: %(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def main() -> None:
    config = load_config()
    _configure_logging(config.log_level)
    logger = logging.getLogger(__name__)

    if not config.otel_enabled:
        logger.debug("OTEL_EXPORTER_OTLP_ENDPOINT not set; skipping telemetry export")
        return

    try:
        raw_stdin = sys.stdin.read()
        raw = json.loads(raw_stdin) if raw_stdin.strip() else {}
    except Exception:
        logger.warning("Could not parse hook payload as JSON; skipping telemetry export")
        return

    try:
        event = parse_hook_event(raw)
    except Exception:
        logger.warning("Could not extract metrics from hook payload", exc_info=True)
        return

    try:
        from . import telemetry

        telemetry.emit(event, config)
    except Exception:
        logger.warning("Failed to export telemetry for hook event", exc_info=True)


if __name__ == "__main__":
    main()
