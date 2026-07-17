"""Outcome injection in SystemMessageBuilder._outcome_section."""

from unittest.mock import MagicMock

import pytest

from interpreter.core.memory.outcomes import OutcomeStore
from interpreter.core.services import system_message_builder as smb
from interpreter.core.services.system_message_builder import SystemMessageBuilder


@pytest.fixture(autouse=True)
def _deterministic_static(monkeypatch):
    monkeypatch.setattr(smb, "_detect_headless", lambda: False)
    smb._system_message_cache.clear()
    yield
    smb._system_message_cache.clear()


def _interp(enable, messages):
    it = MagicMock()
    it.system_message = "BASE PROMPT"
    it.custom_instructions = ""
    it.computer.terminal.languages = []
    it.computer.import_computer_api = False
    it.computer.system_message = ""
    # isolate the outcome section from the others
    it.enable_memory_preprompt = enable
    it.enable_preference_memory = False
    it.preference_store = None
    it.semantic_graph = None  # edit-memory section returns ""
    it.messages = messages
    it._outcome_store = OutcomeStore(db_path=None)  # fresh in-memory, hermetic
    it._last_outcome_scan = 0
    return it


def _code(c):
    return {"role": "assistant", "type": "code", "format": "python", "content": c}


def _console(c):
    return {"role": "computer", "type": "console", "format": "output", "content": c}


def test_injects_recorded_failure():
    msgs = [_code("open('x')"), _console("FileNotFoundError: no x")]
    it = _interp(True, msgs)
    msg = SystemMessageBuilder().build(it)
    assert "## Past failures to avoid repeating" in msg
    assert "FileNotFoundError: no x" in msg
    assert msg.endswith("BASE PROMPT")


def test_disabled_is_byte_identical():
    msgs = [_code("open('x')"), _console("FileNotFoundError: no x")]
    it = _interp(False, msgs)
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"


def test_no_failures_no_section():
    msgs = [_code("print(1)"), _console("1")]
    it = _interp(True, msgs)
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"


def test_incremental_scan_does_not_double_count():
    msgs = [_code("x"), _console("ValueError: bad")]
    it = _interp(True, msgs)
    builder = SystemMessageBuilder()
    builder.build(it)
    builder.build(it)  # same messages, second build
    recurring = it._outcome_store.recurring_failures()
    assert recurring[0]["count"] == 1  # recorded once despite two builds
