"""Task injection in SystemMessageBuilder._tasks_section."""

from unittest.mock import MagicMock

import pytest

from interpreter.core.memory.tasks import TaskStore
from interpreter.core.services import system_message_builder as smb
from interpreter.core.services.system_message_builder import SystemMessageBuilder


@pytest.fixture(autouse=True)
def _deterministic_static(monkeypatch):
    monkeypatch.setattr(smb, "_detect_headless", lambda: False)
    smb._system_message_cache.clear()
    yield
    smb._system_message_cache.clear()


def _interp(enable, query):
    it = MagicMock()
    it.system_message = "BASE PROMPT"
    it.custom_instructions = ""
    it.computer.terminal.languages = []
    it.computer.import_computer_api = False
    it.computer.system_message = ""
    # every other section off — this file tests the task section
    it.enable_memory_preprompt = False
    it.enable_preference_memory = False
    it.enable_outcome_memory = False
    it.enable_context_memory = False
    it.enable_task_memory = enable
    it._task_store = TaskStore(db_path=None)  # fresh in-memory, hermetic
    it._last_task_capture = None
    it.task_limit = 10
    it.messages = [{"role": "user", "type": "message", "content": query}]
    return it


def test_captures_and_injects_open_task():
    it = _interp(True, "let's add the reranker to scout")
    msg = SystemMessageBuilder().build(it)
    assert "## Open tasks" in msg
    assert "add the reranker to scout" in msg
    assert msg.endswith("BASE PROMPT")


def test_disabled_is_byte_identical():
    it = _interp(False, "let's add the reranker")
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"


def test_completion_removes_open_task():
    it = _interp(True, "let's build the outcome store")
    builder = SystemMessageBuilder()
    builder.build(it)
    assert "Open tasks" in builder.build(it)  # persists across builds
    # user completes it next turn
    it.messages = [
        {"role": "user", "type": "message", "content": "done with the outcome store"}
    ]
    msg = SystemMessageBuilder().build(it)
    assert "Open tasks" not in msg


def test_capture_deduplicated_within_turn():
    it = _interp(True, "we need to add reranking")
    builder = SystemMessageBuilder()
    builder.build(it)
    builder.build(it)
    assert len(it._task_store.get_open()) == 1
