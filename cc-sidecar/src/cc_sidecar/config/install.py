"""
cc-sidecar install — install hooks into Claude Code settings.

Supports three scopes matching Claude Code's settings model:
    - user:    ~/.claude/settings.json
    - project: .claude/settings.json (in project root)
    - local:   .claude/settings.local.json (in project root, gitignored)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .hooks import generate_full_settings

# Settings file paths by scope
SCOPE_PATHS = {
    "user": Path.home() / ".claude" / "settings.json",
    "project": Path.cwd() / ".claude" / "settings.json",
    "local": Path.cwd() / ".claude" / "settings.local.json",
}


def _load_settings(path: Path) -> dict[str, Any]:
    """Load existing settings, or return empty dict."""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _merge_settings(
    existing: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, Any]:
    """Merge new settings into existing, preserving non-sidecar config."""
    merged = dict(existing)

    # Merge hooks (add sidecar hooks alongside existing ones)
    existing_hooks = merged.get("hooks", {})
    new_hooks = new.get("hooks", {})
    for event_name, hook_list in new_hooks.items():
        if event_name in existing_hooks:
            # Check if sidecar hook already present
            existing_cmds = {
                h.get("command", "")
                for entry in existing_hooks[event_name]
                for h in entry.get("hooks", [])
            }
            if any("cc-sidecar" in cmd for cmd in existing_cmds):
                continue  # Already installed
            existing_hooks[event_name].extend(hook_list)
        else:
            existing_hooks[event_name] = hook_list
    merged["hooks"] = existing_hooks

    # Set statusLine (override if present)
    if "statusLine" in new:
        merged["statusLine"] = new["statusLine"]

    return merged


def run_install(scope: str = "project", dry_run: bool = False) -> int:
    """Install cc-sidecar hooks into Claude Code settings.

    Args:
        scope: "user", "project", or "local"
        dry_run: If True, print config without writing

    Returns:
        Exit code (0 = success)
    """
    path = SCOPE_PATHS.get(scope)
    if path is None:
        print(f"Unknown scope: {scope}", file=sys.stderr)
        return 1

    # Generate sidecar settings
    sidecar_settings = generate_full_settings()

    if dry_run:
        print(json.dumps(sidecar_settings, indent=2))
        print(f"\n# Would write to: {path}")
        return 0

    # Load and merge
    existing = _load_settings(path)
    merged = _merge_settings(existing, sidecar_settings)

    # Write
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n")

    print(f"Installed cc-sidecar hooks to {path}")
    print(f"  Scope: {scope}")
    print(f"  Events: {len(sidecar_settings['hooks'])} hook events")
    print("  Statusline: enabled")

    return 0


def _remove_sidecar_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    """Remove cc-sidecar hooks from settings, preserving everything else."""
    cleaned = dict(settings)

    if "hooks" in cleaned:
        hooks = cleaned["hooks"]
        for event_name in list(hooks.keys()):
            # Filter out entries that contain cc-sidecar commands
            filtered = []
            for entry in hooks[event_name]:
                entry_hooks = entry.get("hooks", [])
                non_sidecar = [
                    h for h in entry_hooks if "cc-sidecar" not in h.get("command", "")
                ]
                if non_sidecar:
                    filtered.append({**entry, "hooks": non_sidecar})
            if filtered:
                hooks[event_name] = filtered
            else:
                del hooks[event_name]

        if not hooks:
            del cleaned["hooks"]

    # Remove statusline if it references cc-sidecar
    if "statusLine" in cleaned:
        sl = cleaned["statusLine"]
        if isinstance(sl, dict) and "cc-sidecar" in sl.get("command", ""):
            del cleaned["statusLine"]

    return cleaned


def run_uninstall(scope: str = "project") -> int:
    """Remove cc-sidecar hooks from Claude Code settings.

    Args:
        scope: "user", "project", or "local"

    Returns:
        Exit code (0 = success)
    """
    path = SCOPE_PATHS.get(scope)
    if path is None:
        print(f"Unknown scope: {scope}", file=sys.stderr)
        return 1

    if not path.exists():
        print(f"No settings file found at {path}")
        return 0

    existing = _load_settings(path)
    cleaned = _remove_sidecar_hooks(existing)

    if cleaned == existing:
        print(f"No cc-sidecar hooks found in {path}")
        return 0

    path.write_text(json.dumps(cleaned, indent=2) + "\n")
    print(f"Removed cc-sidecar hooks from {path}")
    return 0
