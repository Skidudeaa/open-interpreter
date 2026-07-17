"""Phase 3: MemoryMiddleware records hermes file edits; no-ops for oi."""

from types import SimpleNamespace

import pytest

from interpreter.core.pipeline.memory_middleware import MemoryMiddleware
from interpreter.terminal_interface.components.ui_events import (
    EventType,
    get_event_bus,
    reset_event_bus,
)


class _FakeGraph:
    def __init__(self):
        self.recorded = []

    def record_edit(self, edit):
        self.recorded.append(edit)

    def update_edit_commit_hash(self, *a):
        pass


def _hermes_interp(cwd):
    return SimpleNamespace(
        enable_semantic_memory=True,
        show_file_diffs=True,
        semantic_graph=_FakeGraph(),
        conversation_linker=None,
        auto_commit=False,
        messages=[{"role": "user", "type": "message", "content": "write a file"}],
        computer=SimpleNamespace(cwd=str(cwd)),
    )


@pytest.fixture(autouse=True)
def _bus():
    reset_event_bus()
    yield
    reset_event_bus()


def _events():
    ev = []
    get_event_bus().subscribe_all(lambda e: ev.append(e))
    return ev


def test_records_hermes_file_edits(tmp_path):
    interp = _hermes_interp(tmp_path)
    f = tmp_path / "out.py"
    f.write_text("# before")

    def hermes_stream():
        yield {"role": "assistant", "type": "message", "content": "writing"}
        f.write_text("# after")  # edit happens mid-turn
        yield {
            "role": "computer",
            "type": "console",
            "format": "output",
            "content": "done",
        }

    events = _events()
    ctx = {"interpreter": interp, "backend": "hermes"}
    out = list(MemoryMiddleware().process(hermes_stream(), ctx))

    # Chunks pass through unchanged and in order.
    assert [c.get("content") for c in out] == ["writing", "done"]
    # The edit was recorded to the graph.
    assert len(interp.semantic_graph.recorded) == 1
    # And a FILE_CHANGE event fired for the hermes turn.
    assert any(ev.type == EventType.FILE_CHANGE for ev in events)


def test_oi_backend_is_pure_passthrough(tmp_path):
    interp = _hermes_interp(tmp_path)
    (tmp_path / "x.py").write_text("# before")

    def stream():
        (tmp_path / "x.py").write_text("# after")
        yield {"role": "assistant", "type": "message", "content": "hi"}

    events = _events()
    ctx = {"interpreter": interp, "backend": "oi"}
    out = list(MemoryMiddleware().process(stream(), ctx))

    assert [c.get("content") for c in out] == ["hi"]
    # oi records inline in respond(), NOT here — middleware must do nothing.
    assert interp.semantic_graph.recorded == []
    assert not any(ev.type == EventType.FILE_CHANGE for ev in events)


def test_no_file_change_records_nothing(tmp_path):
    interp = _hermes_interp(tmp_path)
    (tmp_path / "stable.py").write_text("x = 1")

    def stream():
        yield {"role": "assistant", "type": "message", "content": "noop"}

    ctx = {"interpreter": interp, "backend": "hermes"}
    list(MemoryMiddleware().process(stream(), ctx))

    assert interp.semantic_graph.recorded == []
