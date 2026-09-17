"""Best-effort extraction of metrics from a kiro-cli hook JSON payload.

kiro-cli sends session context as JSON on STDIN. The documented fields
(hook_event_name, cwd, session_id) are stable, but per-event fields (tool
name, timing, matcher) vary across triggers and CLI versions. This module
never assumes a field exists -- it pulls out what it can and leaves the
rest as raw attributes, so the hook keeps working across schema drift
instead of crashing on an unexpected payload shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Keys that map cleanly onto span/metric attributes when present. Anything
# else in the payload is still captured, just under a generic prefix.
_KNOWN_STRING_FIELDS = (
    "hook_event_name",
    "cwd",
    "session_id",
    "tool_name",
    "tool",
    "matcher",
)
_KNOWN_DURATION_FIELDS = ("duration_ms", "elapsed_ms", "latency_ms")
_MAX_ATTR_LEN = 512


@dataclass
class HookEvent:
    event_name: str
    session_id: str | None
    cwd: str | None
    tool_name: str | None
    duration_ms: float | None
    attributes: dict[str, str] = field(default_factory=dict)


def _stringify(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= _MAX_ATTR_LEN else text[: _MAX_ATTR_LEN - 3] + "..."


def parse_hook_event(raw: dict[str, Any]) -> HookEvent:
    event_name = str(raw.get("hook_event_name") or raw.get("hook_event") or "unknown")
    session_id = raw.get("session_id")
    cwd = raw.get("cwd")
    tool_name = raw.get("tool_name") or raw.get("tool")

    duration_ms: float | None = None
    for key in _KNOWN_DURATION_FIELDS:
        if key in raw and raw[key] is not None:
            try:
                duration_ms = float(raw[key])
            except (TypeError, ValueError):
                pass
            break

    attributes: dict[str, str] = {}
    for key, value in raw.items():
        if key in _KNOWN_STRING_FIELDS or key in _KNOWN_DURATION_FIELDS:
            continue
        attributes[f"kiro.raw.{key}"] = _stringify(value)

    return HookEvent(
        event_name=event_name,
        session_id=str(session_id) if session_id is not None else None,
        cwd=str(cwd) if cwd is not None else None,
        tool_name=str(tool_name) if tool_name is not None else None,
        duration_ms=duration_ms,
        attributes=attributes,
    )
