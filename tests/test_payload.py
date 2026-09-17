from kiro_mlflow_hook.payload import parse_hook_event


def test_parses_known_fields():
    event = parse_hook_event(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "abc123",
            "cwd": "/repo",
            "tool_name": "fs_write",
            "duration_ms": 42,
        }
    )
    assert event.event_name == "PostToolUse"
    assert event.session_id == "abc123"
    assert event.cwd == "/repo"
    assert event.tool_name == "fs_write"
    assert event.duration_ms == 42.0
    assert event.attributes == {}


def test_accepts_tool_alias_and_elapsed_ms():
    event = parse_hook_event({"hook_event": "AgentStop", "tool": "shell", "elapsed_ms": "10"})
    assert event.event_name == "AgentStop"
    assert event.tool_name == "shell"
    assert event.duration_ms == 10.0


def test_unknown_fields_become_generic_attributes():
    event = parse_hook_event({"hook_event_name": "AgentSpawn", "custom_field": {"nested": 1}})
    assert event.attributes["kiro.raw.custom_field"] == "{'nested': 1}"


def test_missing_event_name_falls_back_to_unknown():
    event = parse_hook_event({})
    assert event.event_name == "unknown"
    assert event.session_id is None
    assert event.duration_ms is None


def test_long_attribute_values_are_truncated():
    event = parse_hook_event({"hook_event_name": "AgentSpawn", "blob": "x" * 1000})
    value = event.attributes["kiro.raw.blob"]
    assert len(value) == 512
    assert value.endswith("...")
