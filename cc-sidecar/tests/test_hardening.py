"""Tests for hardening changes.

Covers:
    - Activity history (append-only table)
    - Session cleanup/TTL
    - Spool size limits
    - Uninstall command
    - Core.py integration (enable_observability flag)
"""

from __future__ import annotations

from unittest.mock import patch

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
    return 1700000000000 + offset_s * 1000


class TestActivityHistory:
    """Activity table stores append-only timeline instead of overwriting."""

    def test_activity_appended_on_eventbus_activity(self, reducer, store):
        """Each ACTIVITY event should create a row in activities table."""
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "eventbus.ACTIVITY",
            "s1",
            {
                "activity_type": "think",
                "message": "Analyzing the codebase",
            },
            _ts(1),
        )
        reducer.handle(
            "eventbus.ACTIVITY",
            "s1",
            {
                "activity_type": "search",
                "message": "Looking for auth middleware",
            },
            _ts(2),
        )
        reducer.handle(
            "eventbus.ACTIVITY",
            "s1",
            {
                "activity_type": "edit",
                "message": "Modifying auth.py",
            },
            _ts(3),
        )

        # All three activities should be in history (most recent first)
        activities = store.get_activities("s1")
        assert len(activities) == 3
        types = [a["activity_type"] for a in activities]
        assert "think" in types
        assert "search" in types
        assert "edit" in types

    def test_activity_filter_by_agent(self, reducer, store):
        """Activities should be filterable by agent_pk."""
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "SubagentStart", "s1", {"agent_id": "a1", "agent_type": "scout"}, _ts(1)
        )

        # Activity from main agent
        reducer.handle(
            "eventbus.ACTIVITY",
            "s1",
            {
                "activity_type": "think",
                "message": "planning",
            },
            _ts(2),
        )

        # Only main agent has activities
        main_activities = store.get_activities("s1", agent_pk="main:s1")
        assert len(main_activities) == 1

        sub_activities = store.get_activities("s1", agent_pk="sub:a1")
        assert len(sub_activities) == 0

    def test_activity_in_session_summary(self, reducer, store):
        """Session summary should include recent activities."""
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "eventbus.ACTIVITY",
            "s1",
            {
                "activity_type": "validate",
                "message": "Running tests",
            },
            _ts(1),
        )

        summary = store.get_session_summary("s1")
        assert "recent_activities" in summary
        assert len(summary["recent_activities"]) == 1

    def test_current_activity_still_updated_on_agent(self, reducer, store):
        """Agent record should still have current_activity_type for live state."""
        reducer.handle("SessionStart", "s1", {"session": {}}, _ts(0))
        reducer.handle(
            "eventbus.ACTIVITY",
            "s1",
            {
                "activity_type": "execute",
                "message": "Running code",
            },
            _ts(1),
        )

        agents = store.get_agents("s1")
        assert agents[0]["current_activity_type"] == "execute"


class TestSessionCleanup:
    """Session cleanup removes old sessions and their dependent data."""

    def test_cleanup_removes_old_sessions(self, store):
        """Sessions older than max_age_days should be removed."""
        import time

        old_ts = round((time.time() - 60 * 86400) * 1000)  # 60 days ago
        recent_ts = round(time.time() * 1000)

        store.upsert_session("old-session", last_seen_at_ms=old_ts, model="opus-4")
        store.upsert_session(
            "recent-session", last_seen_at_ms=recent_ts, model="sonnet-4"
        )

        removed = store.cleanup_old_sessions(max_age_days=30)
        assert removed == 1

        # Old session gone
        assert store.get_session("old-session") is None
        # Recent session preserved
        assert store.get_session("recent-session") is not None

    def test_cleanup_cascades_to_dependents(self, store):
        """Cleanup should remove agents, tool_calls, alerts, files, etc."""
        import time

        old_ts = round((time.time() - 60 * 86400) * 1000)

        store.upsert_session("old-s", last_seen_at_ms=old_ts)
        store.upsert_agent(
            "main:old-s",
            "old-s",
            agent_type="main",
            state="idle",
            state_source="observed",
        )
        store.insert_alert("old-s", "warn", "stuck", "Agent stuck", old_ts)
        store.upsert_file("old-s", "/tmp/foo.py", ownership_source="observed")
        store.insert_activity("old-s", "main:old-s", "think", old_ts, "planning")

        removed = store.cleanup_old_sessions(max_age_days=30)
        assert removed == 1

        assert store.get_agents("old-s") == []
        assert store.get_active_alerts("old-s") == []
        assert store.get_files("old-s") == []
        assert store.get_activities("old-s") == []

    def test_cleanup_returns_zero_when_nothing_old(self, store):
        import time

        recent_ts = round(time.time() * 1000)
        store.upsert_session("fresh", last_seen_at_ms=recent_ts)
        removed = store.cleanup_old_sessions(max_age_days=30)
        assert removed == 0


class TestSpoolSizeLimit:
    """Spool should respect MAX_SPOOL_BYTES."""

    def test_spool_drops_when_limit_reached(self, tmp_path):
        """Events should be silently dropped when spool is full."""
        from cc_sidecar.ingest import transport

        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()

        # Create a large spool file to exceed limit
        large_file = spool_dir / "events_20240101_00.jsonl"
        large_file.write_text("x" * (transport.MAX_SPOOL_BYTES + 1))

        with patch.object(transport, "get_spool_dir", return_value=spool_dir):
            # This should not raise and should not write
            transport._spool_event({"test": "event"})

            # No new spool file created (only the large one we made)
            spool_files = list(spool_dir.glob("events_*.jsonl"))
            assert len(spool_files) == 1


class TestUninstall:
    """Uninstall command removes cc-sidecar hooks from settings."""

    def test_uninstall_removes_hooks(self, tmp_path):
        from cc_sidecar.config.install import _remove_sidecar_hooks

        settings = {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": "cc-sidecar emit"}]}
                ],
                "PostToolUse": [
                    {
                        "hooks": [
                            {"type": "command", "command": "cc-sidecar emit"},
                            {"type": "command", "command": "other-tool"},
                        ]
                    }
                ],
            },
            "statusLine": {"type": "command", "command": "cc-sidecar statusline"},
            "other_setting": True,
        }

        cleaned = _remove_sidecar_hooks(settings)

        # PreToolUse had only sidecar hook → entire event removed
        assert "PreToolUse" not in cleaned.get("hooks", {})

        # PostToolUse had both → only other-tool remains
        assert "PostToolUse" in cleaned.get("hooks", {})
        post_hooks = cleaned["hooks"]["PostToolUse"][0]["hooks"]
        assert len(post_hooks) == 1
        assert post_hooks[0]["command"] == "other-tool"

        # StatusLine removed
        assert "statusLine" not in cleaned

        # Other settings preserved
        assert cleaned["other_setting"] is True

    def test_uninstall_noop_when_no_hooks(self):
        from cc_sidecar.config.install import _remove_sidecar_hooks

        settings = {"other": "value"}
        cleaned = _remove_sidecar_hooks(settings)
        assert cleaned == settings


class TestCLIVersionAndDebug:
    """CLI should support --version and --debug flags."""

    def test_version_flag(self):
        from cc_sidecar.cli import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0

    def test_debug_flag_sets_logging(self):
        import logging

        from cc_sidecar.cli import _setup_logging

        _setup_logging(debug=True)
        assert logging.getLogger().level == logging.DEBUG

        _setup_logging(debug=False)
        assert logging.getLogger().level == logging.WARNING


class TestSchemaVersion:
    """Schema version should be updated for the new activities table."""

    def test_schema_version_is_2(self):
        from cc_sidecar.db.schema import SCHEMA_VERSION

        assert SCHEMA_VERSION == 2

    def test_activities_table_exists(self, store):
        """The activities table should be created by the schema."""
        # This would fail if the table doesn't exist
        activities = store.get_activities("nonexistent-session")
        assert activities == []
