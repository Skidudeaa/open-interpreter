"""Tests for EventStore — SQLite database layer."""

from __future__ import annotations

import pytest

from cc_sidecar.db.store import EventStore


@pytest.fixture
def store(tmp_path):
    """Create a fresh EventStore in a temp directory."""
    db_path = tmp_path / "test.db"
    s = EventStore(db_path)
    yield s
    s.close()


class TestRawEvents:
    def test_insert_and_dedup(self, store):
        """Duplicate payloads should be rejected (idempotent)."""
        payload = {"tool_name": "Read", "file_path": "/tmp/foo.py"}
        row1 = store.insert_raw_event(
            received_at_ms=1000,
            seq=1,
            session_id="s1",
            source_kind="hook",
            event_name="PreToolUse",
            payload=payload,
        )
        assert row1 is not None

        # Same payload → duplicate
        row2 = store.insert_raw_event(
            received_at_ms=1001,
            seq=2,
            session_id="s1",
            source_kind="hook",
            event_name="PreToolUse",
            payload=payload,
        )
        assert row2 is None

    def test_different_payloads_accepted(self, store):
        """Different payloads should be stored separately."""
        row1 = store.insert_raw_event(
            received_at_ms=1000,
            seq=1,
            session_id="s1",
            source_kind="hook",
            event_name="PreToolUse",
            payload={"tool_name": "Read"},
        )
        row2 = store.insert_raw_event(
            received_at_ms=1001,
            seq=2,
            session_id="s1",
            source_kind="hook",
            event_name="PreToolUse",
            payload={"tool_name": "Write"},
        )
        assert row1 is not None
        assert row2 is not None
        assert row1 != row2


class TestSessions:
    def test_upsert_create(self, store):
        store.upsert_session("s1", model="opus-4", cwd="/home/user")
        session = store.get_session("s1")
        assert session is not None
        assert session["model"] == "opus-4"
        assert session["cwd"] == "/home/user"

    def test_upsert_update(self, store):
        store.upsert_session("s1", model="opus-4")
        store.upsert_session("s1", model="sonnet-4", total_cost_usd=0.05)
        session = store.get_session("s1")
        assert session["model"] == "sonnet-4"
        assert session["total_cost_usd"] == 0.05

    def test_get_sessions_ordered(self, store):
        store.upsert_session("s1", last_seen_at_ms=1000)
        store.upsert_session("s2", last_seen_at_ms=2000)
        sessions = store.get_sessions()
        assert len(sessions) == 2
        assert sessions[0]["session_id"] == "s2"  # Most recent first


class TestAgents:
    def test_upsert_and_query(self, store):
        store.upsert_session("s1")
        store.upsert_agent(
            "main:s1",
            "s1",
            agent_type="main",
            state="idle",
            state_source="observed",
        )
        agents = store.get_agents("s1")
        assert len(agents) == 1
        assert agents[0]["state"] == "idle"

    def test_active_agents_filter(self, store):
        store.upsert_session("s1")
        store.upsert_agent(
            "main:s1", "s1", agent_type="main", state="idle", state_source="observed"
        )
        store.upsert_agent(
            "sub:a1",
            "s1",
            agent_type="scout",
            state="finished",
            state_source="observed",
        )
        store.upsert_agent(
            "sub:a2",
            "s1",
            agent_type="surgeon",
            state="running_tool",
            state_source="observed",
        )

        active = store.get_active_agents("s1")
        assert len(active) == 2  # main + surgeon (finished excluded)
        pks = {a["agent_pk"] for a in active}
        assert "sub:a1" not in pks


class TestToolCalls:
    def test_insert_and_close(self, store):
        store.upsert_session("s1")
        store.upsert_agent(
            "main:s1", "s1", agent_type="main", state="idle", state_source="observed"
        )
        store.insert_tool_call(
            "tc1",
            "s1",
            "main:s1",
            "Read",
            1000,
            input_preview="/tmp/foo.py",
        )
        calls = store.get_recent_tool_calls("s1")
        assert len(calls) == 1
        assert calls[0]["status"] == "started"

        store.close_tool_call("tc1", "success", 2000, output_preview="file contents")
        calls = store.get_recent_tool_calls("s1")
        assert calls[0]["status"] == "success"
        assert calls[0]["ended_at_ms"] == 2000


class TestAlerts:
    def test_insert_and_resolve(self, store):
        store.upsert_session("s1")
        alert_id = store.insert_alert("s1", "warn", "stuck", "Agent stuck", 1000)
        active = store.get_active_alerts("s1")
        assert len(active) == 1

        store.resolve_alert(alert_id, 2000)
        active = store.get_active_alerts("s1")
        assert len(active) == 0


class TestSessionSummary:
    def test_summary_aggregation(self, store):
        store.upsert_session("s1", model="opus-4")
        store.upsert_agent(
            "main:s1", "s1", agent_type="main", state="idle", state_source="observed"
        )
        store.upsert_file("s1", "/tmp/foo.py", ownership_source="observed")
        store.insert_alert("s1", "info", "notification", "hello", 1000)

        summary = store.get_session_summary("s1")
        assert summary["session"]["model"] == "opus-4"
        assert summary["active_agent_count"] == 1
        assert summary["files_changed"] == 1
        assert len(summary["active_alerts"]) == 1


class TestColumnAllowlist:
    """Verify that upsert methods reject unknown column names."""

    def test_session_rejects_bad_column(self, store):
        with pytest.raises(ValueError, match="Invalid column names for sessions"):
            store.upsert_session("s1", bad_col="oops")

    def test_session_rejects_injection_attempt(self, store):
        with pytest.raises(ValueError, match="Invalid column names for sessions"):
            store.upsert_session("s1", **{"x = 1; DROP TABLE sessions; --": "pwned"})

    def test_agent_rejects_bad_column(self, store):
        with pytest.raises(ValueError, match="Invalid column names for agents"):
            store.upsert_agent("a1", "s1", bogus_field="nope")

    def test_file_rejects_bad_column(self, store):
        with pytest.raises(ValueError, match="Invalid column names for files"):
            store.upsert_file("s1", "/tmp/f.py", not_a_column=42)

    def test_task_rejects_bad_column(self, store):
        with pytest.raises(ValueError, match="Invalid column names for tasks"):
            store.upsert_task("t1", "s1", sneaky="value")

    def test_session_allows_valid_columns(self, store):
        """All known columns should be accepted without error."""
        store.upsert_session("s1", model="opus", cwd="/tmp", source="startup")
        session = store.get_session("s1")
        assert session["model"] == "opus"
        assert session["cwd"] == "/tmp"

    def test_agent_allows_valid_columns(self, store):
        store.upsert_session("s1")
        store.upsert_agent(
            "main:s1",
            "s1",
            agent_type="main",
            state="running_tool",
            state_source="observed",
            last_tool_name="Read",
        )
        agents = store.get_agents("s1")
        assert agents[0]["state"] == "running_tool"
        assert agents[0]["last_tool_name"] == "Read"

    def test_file_allows_valid_columns(self, store):
        store.upsert_session("s1")
        store.upsert_file(
            "s1",
            "/tmp/f.py",
            ownership_source="observed",
            added_lines=10,
            removed_lines=3,
        )
        files = store.get_files("s1")
        assert files[0]["added_lines"] == 10

    def test_task_allows_valid_columns(self, store):
        store.upsert_session("s1")
        store.upsert_task(
            "t1",
            "s1",
            subject="Fix bug",
            status="running",
            status_source="observed",
        )
        tasks = store.get_tasks("s1")
        assert tasks[0]["subject"] == "Fix bug"
