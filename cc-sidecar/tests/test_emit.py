"""Tests for the emit CLI and transport layer."""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from cc_sidecar.ingest.emit import _detect_event_name, _extract_session_id, run_emit
from cc_sidecar.ingest.transport import _spool_event, get_spool_dir, read_spool_files


class TestEventDetection:
    def test_explicit_event_field(self):
        assert _detect_event_name({"event": "SessionStart"}) == "SessionStart"

    def test_explicit_hook_event_field(self):
        assert _detect_event_name({"hook_event": "PreToolUse"}) == "PreToolUse"

    def test_session_start_heuristic(self):
        payload = {"session": {"id": "s1", "source": "startup"}}
        assert _detect_event_name(payload) == "SessionStart"

    def test_pre_tool_use_heuristic(self):
        payload = {"tool_name": "Read", "input": {"file_path": "/foo"}}
        assert _detect_event_name(payload) == "PreToolUse"

    def test_post_tool_use_heuristic(self):
        payload = {"tool_name": "Read", "output": "contents"}
        assert _detect_event_name(payload) == "PostToolUse"

    def test_post_tool_use_failure_heuristic(self):
        payload = {"tool_name": "Bash", "output": "", "error": "denied"}
        assert _detect_event_name(payload) == "PostToolUseFailure"

    def test_subagent_start_heuristic(self):
        payload = {"agent_id": "abc123", "agent_type": "Explore"}
        assert _detect_event_name(payload) == "SubagentStart"

    def test_subagent_stop_heuristic(self):
        payload = {"agent_id": "abc123", "last_assistant_message": "done"}
        assert _detect_event_name(payload) == "SubagentStop"

    def test_user_prompt_heuristic(self):
        payload = {"prompt": "fix the bug"}
        assert _detect_event_name(payload) == "UserPromptSubmit"

    def test_unknown_payload(self):
        assert _detect_event_name({"random": "data"}) == "Unknown"


class TestSessionIdExtraction:
    def test_direct_session_id(self):
        assert _extract_session_id({"session_id": "s1"}) == "s1"

    def test_nested_session_id(self):
        assert _extract_session_id({"session": {"id": "s2"}}) == "s2"

    def test_env_fallback(self):
        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "env-s3"}):
            assert _extract_session_id({}) == "env-s3"

    def test_unknown_fallback(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove any session env vars
            os.environ.pop("CLAUDE_SESSION_ID", None)
            os.environ.pop("CC_SESSION_ID", None)
            assert _extract_session_id({}) == "unknown"


class TestRunEmit:
    def test_exit_0_on_valid_input(self):
        payload = json.dumps({"event": "SessionStart", "session_id": "s1"})
        with patch("sys.stdin", StringIO(payload)):
            with patch("cc_sidecar.ingest.emit.send_event"):
                result = run_emit()
        assert result == 0

    def test_exit_0_on_empty_input(self):
        with patch("sys.stdin", StringIO("")):
            result = run_emit()
        assert result == 0

    def test_exit_0_on_invalid_json(self):
        with patch("sys.stdin", StringIO("not json at all")):
            with patch("cc_sidecar.ingest.emit.send_event"):
                result = run_emit()
        assert result == 0

    def test_exit_0_on_transport_failure(self):
        payload = json.dumps({"event": "PreToolUse", "session_id": "s1"})
        with patch("sys.stdin", StringIO(payload)):
            with patch("cc_sidecar.ingest.emit.send_event", side_effect=Exception("boom")):
                result = run_emit()
        assert result == 0

    def test_subagent_flag(self):
        payload = json.dumps({"event": "PreToolUse", "session_id": "s1"})
        sent_events = []

        def capture(event):
            sent_events.append(event)

        with patch("sys.stdin", StringIO(payload)):
            with patch("cc_sidecar.ingest.emit.send_event", capture):
                run_emit(subagent=True)

        assert len(sent_events) == 1
        assert sent_events[0]["source_kind"] == "hook_subagent"
        assert sent_events[0]["subagent"] is True

    def test_event_name_override(self):
        payload = json.dumps({"data": "whatever"})
        sent_events = []

        def capture(event):
            sent_events.append(event)

        with patch("sys.stdin", StringIO(payload)):
            with patch("cc_sidecar.ingest.emit.send_event", capture):
                run_emit(event_name_override="CustomEvent")

        assert sent_events[0]["event_name"] == "CustomEvent"


class TestSpoolFallback:
    def test_spool_and_read(self, tmp_path):
        with patch("cc_sidecar.ingest.transport.get_spool_dir", return_value=tmp_path):
            event = {"received_at_ms": 1000, "session_id": "s1", "event_name": "Test"}
            _spool_event(event)

            events = read_spool_files()
            assert len(events) == 1
            assert events[0]["session_id"] == "s1"
            assert events[0]["event_name"] == "Test"

    def test_empty_spool(self, tmp_path):
        with patch("cc_sidecar.ingest.transport.get_spool_dir", return_value=tmp_path):
            events = read_spool_files()
            assert len(events) == 0
