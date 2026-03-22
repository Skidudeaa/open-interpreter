"""
TUI Panels — individual dashboard panels for the Textual app.

Each panel answers one of the four core questions:
    1. What is Claude doing now? (Session + Agents panels)
    2. What changed? (Files panel)
    3. What is blocked? (Alerts panel)
    4. What context is in play? (Context panel)

Plus a Timeline panel for raw event history.

Every datum shows a source badge: [observed] / [reconciled] / [inferred]
"""

from __future__ import annotations

import time
from typing import Any

from rich.text import Text
from textual.widgets import Static


def _source_badge(source: str | None) -> str:
    """Format a source confidence badge."""
    if not source:
        return ""
    badges = {
        "observed": "[green]●[/green]",
        "reconciled": "[yellow]●[/yellow]",
        "inferred": "[red dim]●[/red dim]",
        "unknown": "[dim]○[/dim]",
    }
    return badges.get(source, f"[dim]{source}[/dim]")


def _elapsed(started_ms: int | None, ended_ms: int | None = None) -> str:
    """Format elapsed time."""
    if not started_ms:
        return "—"
    end = ended_ms or int(time.time() * 1000)
    secs = (end - started_ms) / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    elif secs < 3600:
        return f"{secs / 60:.1f}m"
    return f"{secs / 3600:.1f}h"


def _cost(usd: float | None) -> str:
    """Format cost."""
    if usd is None:
        return "—"
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"


def _pct(value: float | None) -> str:
    """Format percentage, showing 'unknown' for None."""
    if value is None:
        return "unknown"
    return f"{value:.0f}%"


# --- State icons ---

AGENT_STATE_ICONS = {
    "idle": "○",
    "running_tool": "◉",
    "awaiting_perm": "⏳",
    "blocked": "⛔",
    "retrying": "↻",
    "compacting": "📦",
    "finished": "✓",
    "finished_warn": "⚠",
    "finished_error": "✗",
    "orphaned": "👻",
}

AGENT_STATE_STYLES = {
    "idle": "dim",
    "running_tool": "cyan bold",
    "awaiting_perm": "yellow",
    "blocked": "red bold",
    "retrying": "yellow",
    "compacting": "magenta",
    "finished": "green",
    "finished_warn": "yellow",
    "finished_error": "red",
    "orphaned": "red dim",
}

ALERT_SEVERITY_STYLES = {
    "info": "blue",
    "warn": "yellow bold",
    "error": "red bold",
}


class SessionPanel(Static):
    """Session overview: model, cwd, context, cost, compactions, active agents."""

    def render_session(self, data: dict[str, Any]) -> str:
        session = data.get("session", {})
        if not session:
            return "[dim]No active session[/dim]"

        model = session.get("model") or "unknown"
        cwd = session.get("cwd") or "—"
        worktree = session.get("worktree_branch")
        ctx_used = _pct(session.get("context_used_pct"))
        ctx_remain = _pct(session.get("context_remaining_pct"))
        cost = _cost(session.get("total_cost_usd"))
        compactions = session.get("compaction_count", 0)
        active = data.get("active_agent_count", 0)
        duration = _elapsed(session.get("started_at_ms"))
        lines_add = session.get("total_lines_added") or 0
        lines_rm = session.get("total_lines_removed") or 0

        wt_info = f" ({worktree})" if worktree else ""
        lines = [
            f"[bold]Session[/bold]  {session.get('session_id', '?')[:12]}",
            f"  model   {model}",
            f"  cwd     {cwd}{wt_info}",
            f"  context {ctx_used} used / {ctx_remain} remaining",
            f"  cost    {cost}  |  duration {duration}",
            f"  lines   [green]+{lines_add}[/green] [red]-{lines_rm}[/red]  |  compactions {compactions}",
            f"  agents  {active} active",
        ]
        return "\n".join(lines)


class AgentsPanel(Static):
    """Agent list: state, elapsed, current tool, resource, last output, source badge."""

    def render_agents(self, agents: list[dict[str, Any]]) -> str:
        if not agents:
            return "[dim]No agents[/dim]"

        lines = ["[bold]Agents[/bold]"]
        for agent in agents:
            state = agent.get("state", "unknown")
            icon = AGENT_STATE_ICONS.get(state, "?")
            style = AGENT_STATE_STYLES.get(state, "")
            badge = _source_badge(agent.get("state_source"))
            agent_type = agent.get("agent_type", "?")
            elapsed = _elapsed(agent.get("started_at_ms"), agent.get("stopped_at_ms"))
            tool = agent.get("last_tool_name") or ""
            resource = agent.get("last_resource") or ""
            activity = agent.get("current_activity_message") or ""
            summary = agent.get("last_summary") or ""

            # First line: icon, type, state, elapsed
            pk = agent.get("agent_pk", "?")
            label = pk.split(":", 1)[-1][:12] if ":" in pk else pk[:12]
            line1 = f"  [{style}]{icon} {agent_type}[/{style}] {label}  {state} {elapsed}  {badge}"
            lines.append(line1)

            # Second line: what it's doing
            if tool or resource:
                lines.append(f"    [dim]{tool}[/dim] {resource}")
            if activity:
                lines.append(f"    [italic]{activity}[/italic]")
            if summary and state.startswith("finished"):
                short = summary[:80].replace("\n", " ")
                lines.append(f"    [dim]{short}[/dim]")

        return "\n".join(lines)


class ContextPanel(Static):
    """Context pane: current ask, last summary, active rules, load reasons."""

    def render_context(self, data: dict[str, Any]) -> str:
        session = data.get("session", {})
        instructions = data.get("instructions", [])

        lines = ["[bold]Context[/bold]"]

        # Current ask
        ask = session.get("current_user_ask", "")
        if ask:
            lines.append(f"  [cyan]ask:[/cyan] {ask[:120]}")
        else:
            lines.append("  [dim]no active ask[/dim]")

        # Last summary
        summary = session.get("last_assistant_summary", "")
        if summary:
            lines.append(f"  [cyan]last:[/cyan] {summary[:120]}")

        # Compaction risk
        ctx_used = session.get("context_used_pct")
        if ctx_used is not None:
            if ctx_used > 90:
                lines.append(f"  [red bold]⚠ context {ctx_used:.0f}% — compaction imminent[/red bold]")
            elif ctx_used > 75:
                lines.append(f"  [yellow]context {ctx_used:.0f}% — compaction possible[/yellow]")

        last_compact = session.get("last_compaction_at_ms")
        if last_compact:
            lines.append(f"  [dim]last compaction: {_elapsed(last_compact)} ago[/dim]")

        # Active instruction sources
        if instructions:
            lines.append("  [cyan]rules:[/cyan]")
            for inst in instructions:
                scope = inst.get("scope", "?")
                reason = inst.get("load_reason", "?")
                path = inst.get("file_path", "?")
                lines.append(f"    [{scope}] {path} ({reason})")
        else:
            lines.append("  [dim]no rules loaded[/dim]")

        return "\n".join(lines)


class FilesPanel(Static):
    """Changed files: path, diffstat, last writer, ownership confidence."""

    def render_files(self, files: list[dict[str, Any]]) -> str:
        if not files:
            return "[bold]Files[/bold]\n  [dim]No changes[/dim]"

        lines = [f"[bold]Files[/bold]  ({len(files)} changed)"]
        for f in files[:30]:  # Cap display
            path = f.get("path", "?")
            added = f.get("added_lines") or 0
            removed = f.get("removed_lines") or 0
            writer = f.get("last_writer_agent_pk", "")
            badge = _source_badge(f.get("ownership_source"))
            git_st = f.get("git_status") or ""

            writer_short = writer.split(":", 1)[-1][:10] if ":" in writer else writer[:10]
            stat = f"[green]+{added}[/green] [red]-{removed}[/red]" if (added or removed) else ""

            lines.append(f"  {path}  {stat}  [dim]{writer_short}[/dim] {badge} {git_st}")

        if len(files) > 30:
            lines.append(f"  [dim]...and {len(files) - 30} more[/dim]")

        return "\n".join(lines)


class AlertsPanel(Static):
    """Alerts: blocked permissions, stale agents, orphan risk, config changes."""

    def render_alerts(self, alerts: list[dict[str, Any]]) -> str:
        if not alerts:
            return "[bold]Alerts[/bold]\n  [green]No active alerts[/green]"

        lines = [f"[bold]Alerts[/bold]  ({len(alerts)} active)"]
        for alert in alerts[:20]:
            severity = alert.get("severity", "info")
            kind = alert.get("kind", "?")
            message = alert.get("message", "?")
            style = ALERT_SEVERITY_STYLES.get(severity, "")
            age = _elapsed(alert.get("created_at_ms"))

            lines.append(f"  [{style}]{severity.upper()}[/{style}] [{kind}] {message[:100]}  [dim]{age} ago[/dim]")

        return "\n".join(lines)


class TimelinePanel(Static):
    """Raw event timeline: append-only event stream, filterable."""

    def render_timeline(self, events: list[dict[str, Any]]) -> str:
        if not events:
            return "[bold]Timeline[/bold]\n  [dim]No events[/dim]"

        lines = [f"[bold]Timeline[/bold]  ({len(events)} events)"]
        for ev in events[:50]:
            ts = ev.get("received_at_ms", 0)
            name = ev.get("event_name", "?")
            source = ev.get("source_kind", "?")
            session = ev.get("session_id", "?")[:8]
            time_str = time.strftime("%H:%M:%S", time.localtime(ts / 1000)) if ts else "??:??:??"

            lines.append(f"  [dim]{time_str}[/dim] [{source}] {name}  [dim]{session}[/dim]")

        return "\n".join(lines)
