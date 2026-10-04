"""Entry point invoked by kiro-cli as a hook command.

kiro-cli runs this as `kiro-otel-hook` (or `python3 -m kiro_otel_hook`),
piping the hook's JSON session context to stdin. The hook is non-blocking,
but a non-zero exit or stack trace still shows up in the session and can be
confused for a real error -- so the process always exits 0.

That guarantee is structural: `cli()` is the only console entry point and
wraps `main()` so nothing, including an exception raised while handling
another exception, can escape it. `main()` additionally catches and logs
each failure stage so the stderr diagnostics are useful.
"""

from __future__ import annotations

import json
import logging
import sys

from .config import GENAI_DETAIL_LEVELS, load_config
from .payload import parse_hook_event


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="kiro-otel-hook: %(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def main() -> None:
    config = load_config()
    _configure_logging(config.log_level)
    logger = logging.getLogger(__name__)

    if config.genai_detail_invalid is not None:
        logger.warning(
            "Ignoring unrecognized KIRO_OTEL_GENAI_DETAIL=%r (expected one of %s); using %r",
            config.genai_detail_invalid,
            ", ".join(GENAI_DETAIL_LEVELS),
            config.genai_detail,
        )

    # This check must stay before the `telemetry` import below: that module
    # loads the OpenTelemetry SDK and exporters, and the disabled path runs
    # on every tool call, so it should cost next to nothing.
    if not config.otel_enabled:
        logger.debug("OTEL_EXPORTER_OTLP_ENDPOINT not set; skipping telemetry export")
        return

    try:
        raw_stdin = sys.stdin.read()
        raw = json.loads(raw_stdin) if raw_stdin.strip() else {}
    except Exception:
        logger.warning("Could not parse hook payload as JSON; skipping telemetry export")
        return
    if not isinstance(raw, dict):
        # Valid JSON but not an object ([], "x", 123): schema drift, not a
        # reason to drop the event. Emit a minimal "unknown" event instead.
        logger.debug("Hook payload is JSON %s, not an object; ignoring its contents", type(raw).__name__)
        raw = {}

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


def cli() -> None:
    """Console-script / `python -m` entry point. Always exits 0."""
    try:
        main()
    except BaseException:  # noqa: BLE001 -- never surface a failure to kiro-cli
        pass
    finally:
        sys.exit(0)


if __name__ == "__main__":
    cli()
