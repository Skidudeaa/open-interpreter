"""Unit tests for the extracted MemoryRecorder service."""

from types import SimpleNamespace

import pytest

import interpreter.core.validation.auto_commit as auto_commit_mod
from interpreter.core.memory.recorder import MemoryRecorder
from interpreter.terminal_interface.components.ui_events import (
    EventType,
    get_event_bus,
    reset_event_bus,
)


class _FakeGraph:
    def __init__(self):
        self.recorded = []
        self.commit_updates = []

    def record_edit(self, edit):
        self.recorded.append(edit)

    def update_edit_commit_hash(self, edit_id, commit_hash):
        self.commit_updates.append((edit_id, commit_hash))


def _fake_interpreter(**overrides):
    base = dict(
        enable_semantic_memory=True,
        semantic_graph=_FakeGraph(),
        conversation_linker=None,
        auto_commit=False,
        messages=[{"role": "user", "type": "message", "content": "do the thing"}],
        computer=SimpleNamespace(cwd="."),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _bus():
    reset_event_bus()
    yield
    reset_event_bus()


def _capture():
    events = []
    get_event_bus().subscribe_all(lambda ev: events.append(ev))
    return events


# --- record_code_execution ------------------------------------------------
def test_record_code_execution_disabled_returns_false():
    interp = _fake_interpreter(enable_semantic_memory=False)
    assert MemoryRecorder().record_code_execution(interp, "print(1)", "python") is False


def test_record_code_execution_no_graph_returns_false():
    interp = _fake_interpreter(semantic_graph=None)
    assert MemoryRecorder().record_code_execution(interp, "print(1)", "python") is False


def test_record_code_execution_currently_noops_due_to_edittype_bug():
    """PINS A PRE-EXISTING LATENT BUG (preserved verbatim by the extraction):
    the inline code used ``EditType.OTHER``, which does not exist on the enum
    (members end at UNKNOWN), so building the Edit raises AttributeError, the
    non-blocking except swallows it, and code-execution recording silently
    no-ops -> returns False, records nothing, emits no MEMORY_RECORD. This is a
    separate bug from the validation gate and is out of scope for the
    behavior-identical decomposition; fixing it (EditType.UNKNOWN) is a
    deliberate follow-up that would update this test.
    """
    interp = _fake_interpreter()
    events = _capture()

    ok = MemoryRecorder().record_code_execution(interp, "print(1)", "python")

    assert ok is False
    assert interp.semantic_graph.recorded == []
    assert not any(ev.type == EventType.MEMORY_RECORD for ev in events)


# --- record_file_changes --------------------------------------------------
def test_record_file_changes_empty_returns_empty():
    interp = _fake_interpreter()
    assert MemoryRecorder().record_file_changes(interp, {}, "msg") == []


def test_record_file_changes_records_each_file():
    interp = _fake_interpreter()
    changed = {
        "/tmp/a.py": ("# old", "# new"),
        "/tmp/b.py": ("", "print('b')"),
    }

    edits = MemoryRecorder().record_file_changes(interp, changed, "msg")

    assert len(edits) == 2
    assert len(interp.semantic_graph.recorded) == 2


# --- commit_edits ---------------------------------------------------------
def test_commit_edits_disabled_returns_none():
    interp = _fake_interpreter(auto_commit=False)
    edit = SimpleNamespace(id="e1", git_commit_hash=None)
    assert MemoryRecorder().commit_edits(interp, [edit]) is None


def test_commit_edits_commits_and_emits(monkeypatch):
    interp = _fake_interpreter(auto_commit=True)
    edit = SimpleNamespace(id="e1", git_commit_hash=None)
    monkeypatch.setattr(auto_commit_mod, "batch_auto_commit", lambda **kw: "abc123")
    events = _capture()

    commit_hash = MemoryRecorder().commit_edits(interp, [edit])

    assert commit_hash == "abc123"
    assert edit.git_commit_hash == "abc123"
    assert interp.semantic_graph.commit_updates == [("e1", "abc123")]
    assert any(ev.type == EventType.GIT_COMMIT for ev in events)
