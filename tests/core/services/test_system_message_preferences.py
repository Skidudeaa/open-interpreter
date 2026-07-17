"""Preference injection + capture in SystemMessageBuilder._preferences_section."""

from unittest.mock import MagicMock

import pytest

from interpreter.core.memory.preferences import PreferenceStore
from interpreter.core.services import system_message_builder as smb
from interpreter.core.services.system_message_builder import SystemMessageBuilder


@pytest.fixture(autouse=True)
def _deterministic_static(monkeypatch):
    monkeypatch.setattr(smb, "_detect_headless", lambda: False)
    smb._system_message_cache.clear()
    yield
    smb._system_message_cache.clear()


def _interp(enable_pref, store, query="I prefer tabs over spaces"):
    it = MagicMock()
    it.system_message = "BASE PROMPT"
    it.custom_instructions = ""
    it.computer.terminal.languages = []
    it.computer.import_computer_api = False
    it.computer.system_message = ""
    # every other section off — this file tests the preference section
    it.enable_memory_preprompt = False
    it.enable_task_memory = False
    it.enable_outcome_memory = False
    it.enable_context_memory = False
    it.enable_preference_memory = enable_pref
    it.preference_store = store
    it.preference_limit = 20
    it.messages = [{"role": "user", "type": "message", "content": query}]
    # real attribute, not a Mock, so the dedup marker compares correctly
    it._last_pref_capture = None
    return it


def test_captures_and_injects_preference():
    store = PreferenceStore(db_path=None)
    it = _interp(True, store, query="I prefer tabs over spaces")
    msg = SystemMessageBuilder().build(it)
    assert "## User preferences" in msg
    assert "Prefer: prefer tabs" in msg
    assert msg.endswith("BASE PROMPT")
    # persisted
    assert any("tabs" in p.text for p in store.get_active())
    store.close()


def test_disabled_is_byte_identical():
    store = PreferenceStore(db_path=None)
    it = _interp(False, store)
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"
    store.close()


def test_no_store_is_no_op():
    it = _interp(True, None)
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"


def test_capture_deduplicated_within_turn():
    store = MagicMock()
    store.get_active.return_value = []
    it = _interp(True, store, query="always run tests")
    builder = SystemMessageBuilder()
    builder.build(it)
    builder.build(it)  # same message, second build
    # record_from_text called only once despite two builds
    assert store.record_from_text.call_count == 1


def test_injects_existing_preferences_even_without_new_declaration():
    store = PreferenceStore(db_path=None)
    store.record_from_text("never force push")
    it = _interp(True, store, query="what's the status?")  # no new declaration
    msg = SystemMessageBuilder().build(it)
    assert "Avoid: avoid force push" in msg
    store.close()


def test_capture_error_is_swallowed():
    store = MagicMock()
    store.record_from_text.side_effect = RuntimeError("db locked")
    it = _interp(True, store, query="I prefer poetry")
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"  # degrades, no raise
