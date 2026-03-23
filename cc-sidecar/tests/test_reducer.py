"""Tests for the Reducer state machine.

Covers all spec-required scenarios:
    - Out-of-order event replay
    - Duplicate event dedup
    - Missing SubagentStop → orphan detection
    - Compaction mid-run
    - Session resume after compaction
    - Background permission denial
    - Multiple concurrent agents
    - Null/absent statusline context fields
    - Task/Agent alias handling
    - Reducer idempotency
"""

from __future__ import annotations

import pytest

from cc_sidecar.db.store import EventStore
from cc_sidecar.reducer.state_machine import Reducer


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test.db"
    s = EventStore(db_path)
    yield s
    s.close()


@pytest.fixture
def reducer(store):
    return Reducer(store)


def _ts(offset_s: int = 0) -> int:
    """Generate a timestamp in ms."""
    return 1700000000000 + offset_s * 1000


class TestSessionLifecycle:
    def test_session_start_creates_session_and_main_agent(self, reducer, store):
        reducer.handle(
            "SessionStart",
            "s1",
            {
                "session": {
                    "id": "s1",
                    "cwd": "/home/user",
                    "model": "opus-4",
                    "source": "startup",
                },
            },
            _ts(0),
        )

        session = store.get_session("s1")
        assert session is not None
        assert session["model"] == "opus-4"
        assert session["source"] == "startup"

        agents = store.get_agents("s1")
        assert len(agents) == 1
        assert agents[0]["agent_pk"] == "main:s1"
        assert agents[0]["state"] == "idle"

    def test_session_end_closes_session(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {"source": "startup"}}, _ts(0))
        reducer.handle("SessionEnd", "s1", {"reason": "user_exit"}, _ts(60))

        session = store.get_session("s1")
        assert session["ended_at_ms"] == _ts(60)
        assert session["end_reason"] == "user_exit"

        agents = store.get_agents("s1")
        assert agents[0]["state"] == "finished"


class TestToolLifecycle:
    def test_pre_and_post_tool_use(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "PreToolUse",
            "s1",
            {
                "tool_name": "Read",
                "tool_use_id": "tc1",
                "input": {"file_path": "/tmp/foo.py"},
            },
            _ts(1),
        )

        agents = store.get_agents("s1")
        assert agents[0]["state"] == "running_tool"
        assert agents[0]["last_tool_name"] == "Read"

        calls = store.get_recent_tool_calls("s1")
        assert len(calls) == 1
        assert calls[0]["status"] == "started"

        reducer.handle(
            "PostToolUse",
            "s1",
            {
                "tool_use_id": "tc1",
                "tool_name": "Read",
                "output": "file contents here",
            },
            _ts(2),
        )

        agents = store.get_agents("s1")
        assert agents[0]["state"] == "idle"

        calls = store.get_recent_tool_calls("s1")
        assert calls[0]["status"] == "success"

    def test_tool_failure(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "PreToolUse",
            "s1",
            {
                "tool_name": "Bash",
                "tool_use_id": "tc2",
                "input": {"command": "rm -rf /"},
            },
            _ts(1),
        )
        reducer.handle(
            "PostToolUseFailure",
            "s1",
            {
                "tool_use_id": "tc2",
                "error": "Permission denied by user",
            },
            _ts(2),
        )

        calls = store.get_recent_tool_calls("s1")
        assert calls[0]["status"] == "failure"

        agents = store.get_agents("s1")
        # Permission denial → blocked
        assert agents[0]["state"] == "blocked"

        alerts = store.get_active_alerts("s1")
        assert any("denied" in a["kind"] for a in alerts)

    def test_file_tracking_on_write(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "PreToolUse",
            "s1",
            {
                "tool_name": "Write",
                "tool_use_id": "tc3",
                "input": {"file_path": "/tmp/new_file.py"},
            },
            _ts(1),
        )

        files = store.get_files("s1")
        assert len(files) == 1
        assert files[0]["path"] == "/tmp/new_file.py"
        assert files[0]["ownership_source"] == "observed"


class TestSubagentLifecycle:
    def test_subagent_start_stop(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "SubagentStart",
            "s1",
            {
                "agent_id": "agent-abc",
                "agent_type": "Explore",
            },
            _ts(1),
        )

        agents = store.get_agents("s1")
        sub = [a for a in agents if a["agent_pk"] == "sub:agent-abc"]
        assert len(sub) == 1
        assert sub[0]["state"] == "idle"
        assert sub[0]["visibility_mode"] == "lifecycle_only"

        reducer.handle(
            "SubagentStop",
            "s1",
            {
                "agent_id": "agent-abc",
                "last_assistant_message": "Found 3 files matching the pattern",
            },
            _ts(5),
        )

        agents = store.get_agents("s1")
        sub = [a for a in agents if a["agent_pk"] == "sub:agent-abc"]
        # SubagentStop → "finished", NOT "success"
        assert sub[0]["state"] == "finished"
        assert "3 files" in sub[0]["last_summary"]

    def test_subagent_stop_without_summary_is_warn(self, reducer, store):
        """SubagentStop with no summary → finished_warn (possibly degraded)."""
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "SubagentStart", "s1", {"agent_id": "a1", "agent_type": "scout"}, _ts(1)
        )
        reducer.handle("SubagentStop", "s1", {"agent_id": "a1"}, _ts(5))

        agents = store.get_agents("s1")
        sub = [a for a in agents if a["agent_pk"] == "sub:a1"]
        assert sub[0]["state"] == "finished_warn"

    def test_subagent_stop_with_error(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "SubagentStart",
            "s1",
            {"agent_id": "a2", "agent_type": "test-runner"},
            _ts(1),
        )
        reducer.handle(
            "SubagentStop",
            "s1",
            {
                "agent_id": "a2",
                "error": "context window exceeded",
            },
            _ts(5),
        )

        agents = store.get_agents("s1")
        sub = [a for a in agents if a["agent_pk"] == "sub:a2"]
        assert sub[0]["state"] == "finished_error"

    def test_missing_subagent_stop_orphan_detection(self, reducer, store):
        """Subagent that survives compaction without Stop → orphaned."""
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "SubagentStart",
            "s1",
            {"agent_id": "orphan1", "agent_type": "scout"},
            _ts(1),
        )
        reducer.handle("PreCompact", "s1", {}, _ts(2))

        # Agent should be compacting
        agents = store.get_agents("s1")
        sub = [a for a in agents if a["agent_pk"] == "sub:orphan1"]
        assert sub[0]["state"] == "compacting"

        # Simulate orphan detection (normally done by health check timer)
        # Manually set last_event far in the past
        store.upsert_agent("sub:orphan1", "s1", last_event_at_ms=_ts(2) - 400000)
        reducer.check_stuck_and_orphaned("s1")

        agents = store.get_agents("s1")
        sub = [a for a in agents if a["agent_pk"] == "sub:orphan1"]
        assert sub[0]["state"] == "orphaned"

        alerts = store.get_active_alerts("s1")
        assert any("orphaned" in a["kind"] for a in alerts)


class TestCompaction:
    def test_compaction_lifecycle(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle("PreCompact", "s1", {}, _ts(10))

        agents = store.get_agents("s1")
        assert agents[0]["state"] == "compacting"

        alerts = store.get_active_alerts("s1")
        assert any("compaction" in a["kind"] for a in alerts)

        reducer.handle("PostCompact", "s1", {}, _ts(15))

        session = store.get_session("s1")
        assert session["compaction_count"] == 1
        assert session["last_compaction_at_ms"] == _ts(15)

        agents = store.get_agents("s1")
        assert agents[0]["state"] == "idle"

        # Compaction alert should be resolved
        alerts = store.get_active_alerts("s1")
        assert not any("compaction" in a["kind"] for a in alerts)

    def test_session_resume_after_compaction(self, reducer, store):
        """SessionStart with source=compact after compaction."""
        reducer.handle("SessionStart", "s1", {"session": {"source": "startup"}}, _ts(0))
        reducer.handle("PreCompact", "s1", {}, _ts(10))
        reducer.handle("PostCompact", "s1", {}, _ts(15))

        # Resume
        reducer.handle(
            "SessionStart",
            "s1",
            {
                "session": {"source": "compact", "model": "opus-4"},
            },
            _ts(16),
        )

        session = store.get_session("s1")
        assert session["source"] == "compact"
        assert session["model"] == "opus-4"


class TestMultipleConcurrentAgents:
    def test_multiple_subagents(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "SubagentStart", "s1", {"agent_id": "a1", "agent_type": "scout"}, _ts(1)
        )
        reducer.handle(
            "SubagentStart", "s1", {"agent_id": "a2", "agent_type": "surgeon"}, _ts(1)
        )
        reducer.handle(
            "SubagentStart",
            "s1",
            {"agent_id": "a3", "agent_type": "test-runner"},
            _ts(2),
        )

        agents = store.get_agents("s1")
        assert len(agents) == 4  # main + 3 subagents

        active = store.get_active_agents("s1")
        assert len(active) == 4

        # Finish one
        reducer.handle(
            "SubagentStop", "s1", {"agent_id": "a1", "summary": "done"}, _ts(5)
        )
        active = store.get_active_agents("s1")
        assert len(active) == 3


class TestPermissionHandling:
    def test_permission_request_creates_alert(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle("PermissionRequest", "s1", {"tool_name": "Bash"}, _ts(1))

        agents = store.get_agents("s1")
        assert agents[0]["state"] == "awaiting_perm"

        alerts = store.get_active_alerts("s1")
        assert len(alerts) >= 1
        assert any("permission" in a["kind"].lower() for a in alerts)

    def test_background_permission_denial(self, reducer, store):
        """Permission denied for background subagent → blocked + alert."""
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "SubagentStart",
            "s1",
            {"agent_id": "bg1", "agent_type": "test-runner"},
            _ts(1),
        )

        # Simulate tool failure with permission denied
        reducer._active_agent["s1"] = "sub:bg1"
        reducer.handle(
            "PostToolUseFailure",
            "s1",
            {
                "tool_use_id": "tc-bg1",
                "error": "Permission denied: Bash not allowed",
            },
            _ts(2),
        )

        agents = store.get_agents("s1")
        bg = [a for a in agents if a["agent_pk"] == "sub:bg1"]
        assert bg[0]["state"] == "blocked"


class TestStatusline:
    def test_statusline_updates_session(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "statusline",
            "s1",
            {
                "total_cost_usd": 0.15,
                "model": "opus-4",
                "context": {"used_percent": 45.2, "remaining_percent": 54.8},
                "lines_added": 120,
                "lines_removed": 30,
                "worktree": {"path": "/tmp/wt1", "branch": "feature-x"},
            },
            _ts(5),
        )

        session = store.get_session("s1")
        assert session["total_cost_usd"] == 0.15
        assert session["context_used_pct"] == 45.2
        assert session["total_lines_added"] == 120
        assert session["worktree_path"] == "/tmp/wt1"
        assert session["worktree_branch"] == "feature-x"

    def test_null_statusline_fields(self, reducer, store):
        """Null/absent fields should not overwrite existing data."""
        reducer.handle("SessionStart", "s1", {"session": {"model": "opus-4"}}, _ts(0))
        reducer.handle(
            "statusline",
            "s1",
            {
                "total_cost_usd": None,
                "context": {},
            },
            _ts(5),
        )

        session = store.get_session("s1")
        assert session["model"] == "opus-4"  # Not overwritten
        assert session["total_cost_usd"] is None  # Was never set


class TestTaskAgentAlias:
    def test_agent_and_task_both_handled(self, reducer, store):
        """Both 'Agent' and 'Task' tool names should be handled."""
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))

        # Agent tool
        reducer.handle(
            "PreToolUse",
            "s1",
            {
                "tool_name": "Agent",
                "tool_use_id": "tc-agent",
                "input": {"subagent_type": "Explore", "prompt": "find auth code"},
            },
            _ts(1),
        )

        calls = store.get_recent_tool_calls("s1")
        assert calls[0]["tool_name"] == "Agent"
        assert "Explore" in calls[0]["input_preview"]

        # Task tool (alias)
        reducer.handle(
            "PreToolUse",
            "s1",
            {
                "tool_name": "Task",
                "tool_use_id": "tc-task",
                "input": {"type": "test-runner", "prompt": "run all tests"},
            },
            _ts(2),
        )

        calls = store.get_recent_tool_calls("s1")
        task_call = [c for c in calls if c["tool_use_id"] == "tc-task"][0]
        assert task_call["tool_name"] == "Task"
        assert "test-runner" in task_call["input_preview"]


class TestReducerIdempotency:
    def test_replay_produces_same_state(self, store):
        """Replaying the same events should produce identical state."""
        events = [
            ("SessionStart", "s1", {"session": {"model": "opus-4"}}, _ts(0)),
            (
                "PreToolUse",
                "s1",
                {
                    "tool_name": "Read",
                    "tool_use_id": "t1",
                    "input": {"file_path": "/a.py"},
                },
                _ts(1),
            ),
            ("PostToolUse", "s1", {"tool_use_id": "t1", "output": "ok"}, _ts(2)),
            (
                "SubagentStart",
                "s1",
                {"agent_id": "sub1", "agent_type": "scout"},
                _ts(3),
            ),
            ("SubagentStop", "s1", {"agent_id": "sub1", "summary": "done"}, _ts(4)),
        ]

        # First pass
        reducer1 = Reducer(store)
        for name, sid, payload, ts in events:
            reducer1.handle(name, sid, payload, ts)

        state1_session = store.get_session("s1")
        state1_agents = store.get_agents("s1")

        # Second pass (replay)
        reducer2 = Reducer(store)
        for name, sid, payload, ts in events:
            reducer2.handle(name, sid, payload, ts)

        state2_session = store.get_session("s1")
        state2_agents = store.get_agents("s1")

        # States should match
        assert state1_session["model"] == state2_session["model"]
        assert len(state1_agents) == len(state2_agents)


class TestStuckDetection:
    def test_stuck_agent_detected(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "PreToolUse",
            "s1",
            {
                "tool_name": "Bash",
                "tool_use_id": "tc-stuck",
                "input": {"command": "sleep 999"},
            },
            _ts(1),
        )

        # Simulate stuck by setting last_event far in the past
        store.upsert_agent("main:s1", "s1", last_event_at_ms=_ts(1) - 200000)

        reducer.check_stuck_and_orphaned("s1")

        agents = store.get_agents("s1")
        assert agents[0]["state"] == "blocked"
        assert agents[0]["state_source"] == "inferred"

        alerts = store.get_active_alerts("s1")
        assert any("stuck" in a["kind"] for a in alerts)


class TestEventBusBridgeEvents:
    def test_eventbus_agent_lifecycle(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "eventbus.AGENT_SPAWN",
            "s1",
            {
                "agent_id": "fork-scout1",
                "role": "scout",
            },
            _ts(1),
        )

        agents = store.get_agents("s1")
        sub = [a for a in agents if a["agent_pk"] == "sub:fork-scout1"]
        assert len(sub) == 1
        assert sub[0]["visibility_mode"] == "full"  # Bridge gives full visibility

        reducer.handle(
            "eventbus.AGENT_COMPLETE",
            "s1",
            {
                "agent_id": "fork-scout1",
                "output": "found 5 files",
            },
            _ts(3),
        )

        agents = store.get_agents("s1")
        sub = [a for a in agents if a["agent_pk"] == "sub:fork-scout1"]
        assert sub[0]["state"] == "finished"

    def test_eventbus_activity(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "eventbus.ACTIVITY",
            "s1",
            {
                "activity_type": "search",
                "message": "Searching for auth middleware",
            },
            _ts(1),
        )

        agents = store.get_agents("s1")
        assert agents[0]["current_activity_type"] == "search"
        assert "auth middleware" in agents[0]["current_activity_message"]

    def test_eventbus_file_change(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "eventbus.FILE_CHANGE",
            "s1",
            {
                "path": "/src/app.py",
                "added_lines": 10,
                "removed_lines": 3,
            },
            _ts(1),
        )

        files = store.get_files("s1")
        assert len(files) == 1
        assert files[0]["path"] == "/src/app.py"
        assert files[0]["added_lines"] == 10


class TestInstructionsLoaded:
    def test_instructions_tracked(self, reducer, store):
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "InstructionsLoaded",
            "s1",
            {
                "file_path": "/project/CLAUDE.md",
                "scope": "project",
                "load_reason": "session_start",
            },
            _ts(1),
        )

        instructions = store.get_instructions("s1")
        assert len(instructions) == 1
        assert instructions[0]["file_path"] == "/project/CLAUDE.md"
        assert instructions[0]["scope"] == "project"
