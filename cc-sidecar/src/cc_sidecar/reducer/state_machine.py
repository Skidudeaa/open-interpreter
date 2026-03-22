"""
Reducer — event-sourced state machine for cc-sidecar.

Maps raw events (from hooks, statusline, eventbus) into materialized state
in the EventStore. The reducer is idempotent: replaying the same events
produces the same state.

Key invariants:
    - SubagentStop means "finished responding", NOT "succeeded"
    - Every state gets a state_source tag (observed | inferred)
    - Main thread is modeled as synthetic agent main:<session_id>
    - Both "Task" and "Agent" tool names are accepted (alias since CC 2.1.63)
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .resource_extractor import AGENT_TOOL_ALIASES, extract_resource

if TYPE_CHECKING:
    from ..db.store import EventStore

logger = logging.getLogger(__name__)

# Agent/Task tool name aliases
_AGENT_TOOL_NAMES = AGENT_TOOL_ALIASES

# How long (ms) before an agent is considered stuck
STUCK_THRESHOLD_MS = 120_000  # 2 minutes

# How long (ms) before an agent surviving compaction is orphaned
ORPHAN_THRESHOLD_MS = 300_000  # 5 minutes


class Reducer:
    """Event-sourced state machine.

    Processes raw events and updates materialized state in the store.
    Designed to be idempotent — safe to replay events.
    """

    def __init__(self, store: EventStore):
        self._store = store
        # Track current active agent per session for tool attribution
        self._active_agent: dict[str, str] = {}  # session_id -> agent_pk
        # Track pending compaction alerts by session
        self._compaction_alerts: dict[str, int] = {}  # session_id -> alert_id

    def handle(
        self,
        event_name: str,
        session_id: str,
        payload: dict[str, Any],
        received_at_ms: int,
    ) -> None:
        """Dispatch an event to the appropriate handler.

        This is the main entry point. Called by the daemon after storing the raw event.
        """
        handler = self._HANDLERS.get(event_name)
        if handler:
            try:
                handler(self, event_name, session_id, payload, received_at_ms)
            except Exception:
                logger.exception(
                    "Reducer error handling %s for session %s", event_name, session_id
                )
        else:
            logger.debug("No handler for event: %s", event_name)

    # --- Event handlers ---

    def _handle_session_start(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        session_data = payload.get("session", payload)
        self._store.upsert_session(
            session_id,
            cwd=session_data.get("cwd"),
            project_dir=session_data.get("project_dir"),
            model=session_data.get("model"),
            claude_version=session_data.get("claude_version"),
            source=session_data.get("source", "startup"),
            started_at_ms=ts,
            last_seen_at_ms=ts,
        )
        # Create synthetic main agent
        main_pk = f"main:{session_id}"
        self._store.upsert_agent(
            main_pk,
            session_id,
            agent_type="main",
            state="idle",
            state_source="observed",
            started_at_ms=ts,
            last_event_at_ms=ts,
            visibility_mode="full",
        )
        self._active_agent[session_id] = main_pk

    def _handle_session_end(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        self._store.upsert_session(
            session_id,
            ended_at_ms=ts,
            last_seen_at_ms=ts,
            end_reason=payload.get("reason", "normal"),
        )
        # Mark main agent finished
        main_pk = f"main:{session_id}"
        self._store.upsert_agent(
            main_pk,
            session_id,
            state="finished",
            state_source="observed",
            stopped_at_ms=ts,
            last_event_at_ms=ts,
        )

    def _handle_user_prompt_submit(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        prompt = payload.get("prompt", payload.get("message", ""))
        self._store.upsert_session(
            session_id, current_user_ask=prompt, last_seen_at_ms=ts
        )
        # Main agent is now active
        main_pk = f"main:{session_id}"
        self._store.upsert_agent(
            main_pk,
            session_id,
            state="idle",
            state_source="observed",
            last_event_at_ms=ts,
        )
        self._active_agent[session_id] = main_pk

    def _handle_pre_tool_use(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        tool_name = payload.get("tool_name", payload.get("tool", "unknown"))
        tool_use_id = payload.get("tool_use_id", f"synth-{ts}")
        tool_input = payload.get("input", payload.get("tool_input", {}))

        # Determine agent
        agent_pk = self._active_agent.get(session_id, f"main:{session_id}")

        # If this is an Agent/Task tool, we'll see SubagentStart next
        resource = extract_resource(tool_name, tool_input)

        self._store.insert_tool_call(
            tool_use_id=tool_use_id,
            session_id=session_id,
            agent_pk=agent_pk,
            tool_name=tool_name,
            started_at_ms=ts,
            input_preview=resource,
        )

        self._store.upsert_agent(
            agent_pk,
            session_id,
            state="running_tool",
            state_source="observed",
            last_tool_name=tool_name,
            last_resource=resource,
            last_event_at_ms=ts,
        )

        # Track file writes
        if tool_name in ("Write", "Edit"):
            path = tool_input.get("file_path", tool_input.get("path"))
            if path:
                self._store.upsert_file(
                    session_id,
                    path,
                    last_writer_agent_pk=agent_pk,
                    ownership_source="observed",
                    last_changed_at_ms=ts,
                )

        self._store.upsert_session(session_id, last_seen_at_ms=ts)

    def _handle_permission_request(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        agent_pk = self._active_agent.get(session_id, f"main:{session_id}")
        self._store.upsert_agent(
            agent_pk,
            session_id,
            state="awaiting_perm",
            state_source="observed",
            last_event_at_ms=ts,
        )
        tool_name = payload.get("tool_name", "unknown")
        self._store.insert_alert(
            session_id=session_id,
            severity="warn",
            kind="permission_denied",
            message=f"Permission requested for {tool_name}",
            created_at_ms=ts,
        )

    def _handle_post_tool_use(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        tool_use_id = payload.get("tool_use_id", "")
        output = payload.get("output", payload.get("result", ""))

        if tool_use_id:
            output_preview = str(output)[:200] if output else None
            self._store.close_tool_call(
                tool_use_id, "success", ts, output_preview=output_preview
            )

        agent_pk = self._active_agent.get(session_id, f"main:{session_id}")
        summary = str(output)[:200] if output else None
        self._store.upsert_agent(
            agent_pk,
            session_id,
            state="idle",
            state_source="observed",
            last_summary=summary,
            last_event_at_ms=ts,
        )
        self._store.upsert_session(session_id, last_seen_at_ms=ts)

    def _handle_post_tool_use_failure(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        tool_use_id = payload.get("tool_use_id", "")
        error = payload.get("error", payload.get("message", "unknown error"))

        if tool_use_id:
            self._store.close_tool_call(
                tool_use_id, "failure", ts, error=str(error)[:500]
            )

        agent_pk = self._active_agent.get(session_id, f"main:{session_id}")

        # Classify failure
        error_str = str(error).lower()
        if "denied" in error_str or "permission" in error_str:
            state = "blocked"
            self._store.insert_alert(
                session_id=session_id,
                severity="error",
                kind="permission_denied",
                message=f"Permission denied: {error}",
                created_at_ms=ts,
            )
        elif "retry" in error_str or "timeout" in error_str:
            state = "retrying"
        else:
            state = "idle"  # back to idle after non-fatal error

        self._store.upsert_agent(
            agent_pk,
            session_id,
            state=state,
            state_source="observed",
            last_event_at_ms=ts,
        )

    def _handle_notification(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        notification_type = payload.get("type", payload.get("notification_type", ""))
        message = payload.get("message", payload.get("text", ""))

        severity = "info"
        kind = "notification"
        if "permission" in str(notification_type).lower():
            severity = "warn"
            kind = "permission_denied"
        elif "error" in str(notification_type).lower():
            severity = "error"
            kind = "error"

        self._store.insert_alert(
            session_id=session_id,
            severity=severity,
            kind=kind,
            message=str(message)[:500],
            created_at_ms=ts,
        )

    def _handle_subagent_start(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        agent_id = payload.get("agent_id", payload.get("id", f"sub-{ts}"))
        agent_type = payload.get("agent_type", payload.get("type", "subagent"))
        agent_pk = f"sub:{agent_id}"

        self._store.upsert_agent(
            agent_pk,
            session_id,
            agent_id=agent_id,
            agent_type=agent_type,
            state="idle",
            state_source="observed",
            started_at_ms=ts,
            last_event_at_ms=ts,
            # Settings-level hooks only give lifecycle, not per-tool
            visibility_mode="lifecycle_only",
        )

    def _handle_subagent_stop(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        agent_id = payload.get("agent_id", payload.get("id", ""))
        agent_pk = f"sub:{agent_id}"
        summary = payload.get("last_assistant_message", payload.get("summary", ""))
        error = payload.get("error")

        # SubagentStop means "finished responding", NOT "succeeded"
        if error:
            state = "finished_error"
        elif not summary:
            state = "finished_warn"  # no summary = possibly degraded
        else:
            state = "finished"

        self._store.upsert_agent(
            agent_pk,
            session_id,
            state=state,
            state_source="observed",
            stopped_at_ms=ts,
            last_event_at_ms=ts,
            last_summary=str(summary)[:500] if summary else None,
        )

    def _handle_task_completed(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        task_id = payload.get("task_id", payload.get("id", f"task-{ts}"))
        subject = payload.get("subject", payload.get("title", "unknown"))

        self._store.upsert_task(
            task_id,
            session_id,
            subject=subject,
            status="completed",
            status_source="observed",
            completed_at_ms=ts,
        )

    def _handle_pre_compact(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        # Mark all active agents as compacting
        agents = self._store.get_active_agents(session_id)
        for agent in agents:
            self._store.upsert_agent(
                agent["agent_pk"],
                session_id,
                state="compacting",
                state_source="observed",
                last_event_at_ms=ts,
            )
        alert_id = self._store.insert_alert(
            session_id=session_id,
            severity="warn",
            kind="compaction",
            message="Context compaction in progress — agents may lose state",
            created_at_ms=ts,
        )
        self._compaction_alerts[session_id] = alert_id

    def _handle_post_compact(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        # Increment compaction counter
        session = self._store.get_session(session_id)
        count = (session.get("compaction_count", 0) if session else 0) + 1
        self._store.upsert_session(
            session_id,
            compaction_count=count,
            last_compaction_at_ms=ts,
            last_seen_at_ms=ts,
        )
        # Restore main agent to idle
        main_pk = f"main:{session_id}"
        self._store.upsert_agent(
            main_pk,
            session_id,
            state="idle",
            state_source="observed",
            last_event_at_ms=ts,
        )
        # Resolve compaction alert
        alert_id = self._compaction_alerts.pop(session_id, None)
        if alert_id:
            self._store.resolve_alert(alert_id, ts)

    def _handle_config_change(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        change_type = payload.get("type", "settings")
        detail = payload.get("detail", payload.get("message", ""))
        self._store.insert_alert(
            session_id=session_id,
            severity="info",
            kind="config_change",
            message=f"{change_type}: {detail}"[:500],
            created_at_ms=ts,
        )

    def _handle_instructions_loaded(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        file_path = payload.get("file_path", payload.get("path", ""))
        scope = payload.get("scope")
        load_reason = payload.get("load_reason", payload.get("reason"))
        if file_path:
            self._store.upsert_instruction(
                session_id,
                file_path,
                scope=scope,
                load_reason=load_reason,
                loaded_at_ms=ts,
            )

    def _handle_stop(self, _name: str, session_id: str, payload: dict, ts: int) -> None:
        # Stop hook — session ending, keep cleanup trivial
        self._store.upsert_session(session_id, last_seen_at_ms=ts)
        summary = payload.get("last_assistant_message", "")
        if summary:
            self._store.upsert_session(
                session_id, last_assistant_summary=str(summary)[:1000]
            )

    def _handle_statusline(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        """Handle statusline heartbeat (JSON from statusline script)."""
        updates: dict[str, Any] = {"last_seen_at_ms": ts}

        # Map statusline fields to session columns
        if "total_cost_usd" in payload and payload["total_cost_usd"] is not None:
            updates["total_cost_usd"] = payload["total_cost_usd"]
        if "model" in payload and payload["model"]:
            updates["model"] = payload["model"]
        if "session" in payload and isinstance(payload["session"], dict):
            s = payload["session"]
            if s.get("id"):
                session_id = s["id"]  # Use actual session id
        # Context window
        if "context" in payload and isinstance(payload["context"], dict):
            ctx = payload["context"]
            if ctx.get("used_percent") is not None:
                updates["context_used_pct"] = ctx["used_percent"]
            if ctx.get("remaining_percent") is not None:
                updates["context_remaining_pct"] = ctx["remaining_percent"]
        # Lines
        if "lines_added" in payload and payload["lines_added"] is not None:
            updates["total_lines_added"] = payload["lines_added"]
        if "lines_removed" in payload and payload["lines_removed"] is not None:
            updates["total_lines_removed"] = payload["lines_removed"]
        # Worktree
        if "worktree" in payload and isinstance(payload["worktree"], dict):
            wt = payload["worktree"]
            if wt.get("path"):
                updates["worktree_path"] = wt["path"]
            if wt.get("branch"):
                updates["worktree_branch"] = wt["branch"]
        # Duration
        if "total_duration_ms" in payload and payload["total_duration_ms"] is not None:
            updates["total_duration_ms"] = payload["total_duration_ms"]

        self._store.upsert_session(session_id, **updates)

    # --- EventBus bridge events (from fork's internal event system) ---

    def _handle_eventbus_agent_spawn(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        agent_id = payload.get("agent_id", payload.get("id", f"fork-{ts}"))
        role = payload.get("role", "custom")
        agent_pk = f"sub:{agent_id}"
        self._store.upsert_agent(
            agent_pk,
            session_id,
            agent_id=agent_id,
            agent_type=role,
            state="idle",
            state_source="observed",
            started_at_ms=ts,
            last_event_at_ms=ts,
            visibility_mode="full",  # Fork bridge gives full visibility
        )

    def _handle_eventbus_agent_complete(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        agent_id = payload.get("agent_id", payload.get("id", ""))
        agent_pk = f"sub:{agent_id}"
        output = payload.get("output", "")
        self._store.upsert_agent(
            agent_pk,
            session_id,
            state="finished",
            state_source="observed",
            stopped_at_ms=ts,
            last_event_at_ms=ts,
            last_summary=str(output)[:500] if output else None,
        )

    def _handle_eventbus_agent_error(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        agent_id = payload.get("agent_id", payload.get("id", ""))
        agent_pk = f"sub:{agent_id}"
        error = payload.get("error", "")
        self._store.upsert_agent(
            agent_pk,
            session_id,
            state="finished_error",
            state_source="observed",
            stopped_at_ms=ts,
            last_event_at_ms=ts,
            last_summary=str(error)[:500],
        )

    def _handle_eventbus_activity(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        agent_pk = self._active_agent.get(session_id, f"main:{session_id}")
        activity_type = payload.get("activity_type", payload.get("type", ""))
        message = str(payload.get("message", ""))[:200]
        # Update current activity on agent (live state)
        self._store.upsert_agent(
            agent_pk,
            session_id,
            current_activity_type=activity_type,
            current_activity_message=message,
            last_event_at_ms=ts,
        )
        # Append to activity history (durable timeline)
        if activity_type:
            self._store.insert_activity(
                session_id=session_id,
                agent_pk=agent_pk,
                activity_type=activity_type,
                started_at_ms=ts,
                message=message or None,
            )

    def _handle_eventbus_file_change(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        path = payload.get("path", payload.get("file_path", ""))
        if path:
            agent_pk = self._active_agent.get(session_id, f"main:{session_id}")
            self._store.upsert_file(
                session_id,
                path,
                last_writer_agent_pk=agent_pk,
                ownership_source="observed",
                added_lines=payload.get("added_lines"),
                removed_lines=payload.get("removed_lines"),
                last_changed_at_ms=ts,
            )

    def _handle_eventbus_token_update(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        updates = {"last_seen_at_ms": ts}
        if "context_used_pct" in payload:
            updates["context_used_pct"] = payload["context_used_pct"]
        if "context_remaining_pct" in payload:
            updates["context_remaining_pct"] = payload["context_remaining_pct"]
        self._store.upsert_session(session_id, **updates)

    def _handle_eventbus_test(
        self, _name: str, session_id: str, payload: dict, ts: int
    ) -> None:
        """Handle TEST_START/TEST_END from fork EventBus."""
        status = payload.get("status", "running")
        if status in ("failed", "error"):
            self._store.insert_alert(
                session_id=session_id,
                severity="warn",
                kind="test_failure",
                message=f"Test {status}: {payload.get('message', '')}",
                created_at_ms=ts,
            )

    # --- Stuck/orphan detection ---

    def check_stuck_and_orphaned(self, session_id: str) -> None:
        """Check for stuck or orphaned agents. Call periodically."""
        now_ms = int(time.time() * 1000)
        agents = self._store.get_active_agents(session_id)

        for agent in agents:
            last_event = agent.get("last_event_at_ms", 0)
            if not last_event:
                continue
            elapsed = now_ms - last_event
            state = agent["state"]

            if state == "compacting" and elapsed > ORPHAN_THRESHOLD_MS:
                # Survived compaction too long → orphaned
                self._store.upsert_agent(
                    agent["agent_pk"],
                    session_id,
                    state="orphaned",
                    state_source="inferred",
                    last_event_at_ms=now_ms,
                )
                self._store.insert_alert(
                    session_id=session_id,
                    severity="error",
                    kind="orphaned",
                    message=f"Agent {agent['agent_pk']} orphaned after compaction",
                    created_at_ms=now_ms,
                )
            elif (
                state in ("running_tool", "awaiting_perm", "idle")
                and elapsed > STUCK_THRESHOLD_MS
            ):
                self._store.upsert_agent(
                    agent["agent_pk"],
                    session_id,
                    state="blocked",
                    state_source="inferred",
                    last_event_at_ms=now_ms,
                )
                self._store.insert_alert(
                    session_id=session_id,
                    severity="warn",
                    kind="stuck",
                    message=f"Agent {agent['agent_pk']} stuck in {state} for {elapsed // 1000}s",
                    created_at_ms=now_ms,
                )

    # --- Handler registry ---

    _HANDLERS: dict[str, Any] = {}


# Populate handler registry
Reducer._HANDLERS = {
    # Claude Code hooks
    "SessionStart": Reducer._handle_session_start,
    "SessionEnd": Reducer._handle_session_end,
    "UserPromptSubmit": Reducer._handle_user_prompt_submit,
    "PreToolUse": Reducer._handle_pre_tool_use,
    "PermissionRequest": Reducer._handle_permission_request,
    "PostToolUse": Reducer._handle_post_tool_use,
    "PostToolUseFailure": Reducer._handle_post_tool_use_failure,
    "Notification": Reducer._handle_notification,
    "SubagentStart": Reducer._handle_subagent_start,
    "SubagentStop": Reducer._handle_subagent_stop,
    "TaskCompleted": Reducer._handle_task_completed,
    "PreCompact": Reducer._handle_pre_compact,
    "PostCompact": Reducer._handle_post_compact,
    "ConfigChange": Reducer._handle_config_change,
    "InstructionsLoaded": Reducer._handle_instructions_loaded,
    "Stop": Reducer._handle_stop,
    # Statusline heartbeats
    "statusline": Reducer._handle_statusline,
    # Fork EventBus bridge events
    "eventbus.AGENT_SPAWN": Reducer._handle_eventbus_agent_spawn,
    "eventbus.AGENT_COMPLETE": Reducer._handle_eventbus_agent_complete,
    "eventbus.AGENT_ERROR": Reducer._handle_eventbus_agent_error,
    "eventbus.ACTIVITY": Reducer._handle_eventbus_activity,
    "eventbus.FILE_CHANGE": Reducer._handle_eventbus_file_change,
    "eventbus.SYSTEM_TOKEN_UPDATE": Reducer._handle_eventbus_token_update,
    "eventbus.TEST_START": Reducer._handle_eventbus_test,
    "eventbus.TEST_END": Reducer._handle_eventbus_test,
}
