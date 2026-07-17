"""Context-pattern injection in SystemMessageBuilder._context_section."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from interpreter.core.memory.context_patterns import ContextPatternStore
from interpreter.core.memory.outcomes import OutcomeStore
from interpreter.core.memory.tasks import TaskStore
from interpreter.core.services import system_message_builder as smb
from interpreter.core.services.system_message_builder import SystemMessageBuilder


@pytest.fixture(autouse=True)
def _deterministic(monkeypatch):
    monkeypatch.setattr(smb, "_detect_headless", lambda: False)
    smb._system_message_cache.clear()
    # Freeze the clock at 02:00 -> "late night" bucket.
    from interpreter.core.memory import context_patterns as cp

    monkeypatch.setattr(cp, "_now", lambda: datetime(2026, 7, 17, 2, 0))
    yield
    smb._system_message_cache.clear()


def _interp(enable, query, context_store):
    it = MagicMock()
    it.system_message = "BASE PROMPT"
    it.custom_instructions = ""
    it.computer.terminal.languages = []
    it.computer.import_computer_api = False
    it.computer.system_message = ""
    it.enable_memory_preprompt = enable
    it.enable_preference_memory = False
    it.preference_store = None
    it.semantic_graph = None
    it._context_store = context_store
    it._last_context_capture = None
    # neutralize sibling sections
    it._task_store = TaskStore(db_path=None)
    it._last_task_capture = None
    it.task_limit = 10
    it._outcome_store = OutcomeStore(db_path=None)
    it._last_outcome_scan = 0
    it.messages = [{"role": "user", "type": "message", "content": query}]
    return it


def test_no_hint_without_established_pattern():
    store = ContextPatternStore(db_path=None)
    it = _interp(True, "fix the failing test", store)
    # first observation -> below evidence threshold -> no section
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"


def test_injects_hint_once_pattern_established():
    store = ContextPatternStore(db_path=None)
    for _ in range(3):
        store.record("late night", "debug")
    it = _interp(True, "help me", store)  # "help me" -> other, no new record
    msg = SystemMessageBuilder().build(it)
    assert "## Working context" in msg
    assert "late night" in msg and "debug" in msg


def test_disabled_is_byte_identical():
    store = ContextPatternStore(db_path=None)
    for _ in range(5):
        store.record("late night", "debug")
    it = _interp(False, "fix bug", store)
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"


def test_capture_deduplicated_within_turn():
    store = ContextPatternStore(db_path=None)
    it = _interp(True, "debug the crash", store)
    builder = SystemMessageBuilder()
    builder.build(it)
    builder.build(it)
    # one debug observation recorded despite two builds
    rows = store._conn.execute(
        "SELECT count FROM context_patterns WHERE bucket='late night' AND activity='debug'"
    ).fetchone()
    assert rows["count"] == 1
