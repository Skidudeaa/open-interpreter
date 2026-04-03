"""
Hook configuration templates for Claude Code settings.

Generates the hooks and statusline config for cc-sidecar integration.
Covers all 16 observable event types. Does NOT include WorktreeCreate
(that hook replaces Claude's default behavior and requires outputting the path).
"""

from __future__ import annotations

from typing import Any

# All hook events the sidecar observes
HOOK_EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "SubagentStart",
    "SubagentStop",
    "TaskCompleted",
    "ConfigChange",
    "InstructionsLoaded",
    "PreCompact",
    "PostCompact",
    "Stop",
    "SessionEnd",
]

# Default emit command
DEFAULT_EMIT_CMD = "cc-sidecar emit"

# Default statusline command
DEFAULT_STATUSLINE_CMD = "cc-sidecar statusline"


def generate_hooks_config(
    emit_cmd: str = DEFAULT_EMIT_CMD,
    events: list[str] | None = None,
) -> dict[str, Any]:
    """Generate the hooks section of Claude Code settings.

    Args:
        emit_cmd: The command to run for each hook event.
        events: Subset of events to hook. None = all events.

    Returns:
        Dict suitable for merging into settings.json "hooks" key.
    """
    target_events = events or HOOK_EVENTS
    hooks: dict[str, Any] = {}

    for event_name in target_events:
        hooks[event_name] = [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": emit_cmd,
                    }
                ]
            }
        ]

    return hooks


def generate_statusline_config(
    statusline_cmd: str = DEFAULT_STATUSLINE_CMD,
) -> dict[str, Any]:
    """Generate the statusLine section of Claude Code settings."""
    return {
        "type": "command",
        "command": statusline_cmd,
    }


def generate_full_settings(
    emit_cmd: str = DEFAULT_EMIT_CMD,
    statusline_cmd: str = DEFAULT_STATUSLINE_CMD,
    events: list[str] | None = None,
) -> dict[str, Any]:
    """Generate complete cc-sidecar settings for Claude Code.

    Returns a dict with both hooks and statusLine keys.
    """
    return {
        "hooks": generate_hooks_config(emit_cmd, events),
        "statusLine": generate_statusline_config(statusline_cmd),
    }


def generate_subagent_frontmatter(
    name: str = "custom-agent",
    description: str = "Custom agent with full sidecar telemetry",
    emit_cmd: str = DEFAULT_EMIT_CMD,
) -> str:
    """Generate YAML frontmatter for a custom subagent with full telemetry.

    This gives Mode B (full telemetry) visibility for custom subagents.
    Built-in subagents only get Mode A (lifecycle-only).
    """
    emit_sub = f"{emit_cmd} --subagent"
    return f"""---
name: {name}
description: {description}
hooks:
  PreToolUse:
    - matcher: ".*"
      hooks:
        - type: command
          command: "{emit_sub}"
  PostToolUse:
    - matcher: ".*"
      hooks:
        - type: command
          command: "{emit_sub}"
  PostToolUseFailure:
    - matcher: ".*"
      hooks:
        - type: command
          command: "{emit_sub}"
  Stop:
    - hooks:
        - type: command
          command: "{emit_sub}"
---"""
