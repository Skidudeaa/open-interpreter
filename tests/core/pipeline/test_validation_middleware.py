"""Phase 4: post-hoc warn-only ValidationMiddleware for hermes edits."""

from types import SimpleNamespace

import pytest

from interpreter.core.pipeline.validation_middleware import ValidationMiddleware
from interpreter.core.validation.syntax_checker import SyntaxChecker
from interpreter.terminal_interface.components.ui_events import (
    EventType,
    get_event_bus,
    reset_event_bus,
)


def _hermes_interp(cwd, enable_validation=True):
    return SimpleNamespace(
        enable_validation=enable_validation,
        syntax_checker=SyntaxChecker() if enable_validation else None,
        computer=SimpleNamespace(cwd=str(cwd)),
        messages=[{"role": "user", "type": "message", "content": "edit a file"}],
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


def _stream_writing(path, content):
    def gen():
        yield {"role": "assistant", "type": "message", "content": "editing"}
        path.write_text(content)
        yield {
            "role": "computer",
            "type": "console",
            "format": "output",
            "content": "done",
        }

    return gen()


def test_invalid_hermes_edit_warns(tmp_path):
    interp = _hermes_interp(tmp_path)
    f = tmp_path / "broken.py"
    f.write_text("x = 1")

    events = _events()
    ctx = {"interpreter": interp, "backend": "hermes"}
    out = list(
        ValidationMiddleware().process(
            _stream_writing(f, "def broken(:\n    pass"), ctx
        )
    )

    # Original chunks pass through, and a [Validation] warning is appended after.
    assert [c.get("content") for c in out[:2]] == ["editing", "done"]
    warnings = [c for c in out if "[Validation]" in (c.get("content") or "")]
    assert warnings, "invalid edit should append a [Validation] chunk"
    types = {ev.type for ev in events}
    assert EventType.VALIDATION_START in types and EventType.VALIDATION_END in types


def test_valid_hermes_edit_no_warning(tmp_path):
    interp = _hermes_interp(tmp_path)
    f = tmp_path / "ok.py"
    f.write_text("x = 1")

    ctx = {"interpreter": interp, "backend": "hermes"}
    out = list(ValidationMiddleware().process(_stream_writing(f, "y = 2"), ctx))

    assert not any("[Validation]" in (c.get("content") or "") for c in out)


def test_oi_backend_passthrough(tmp_path):
    interp = _hermes_interp(tmp_path)
    f = tmp_path / "z.py"
    f.write_text("x = 1")

    ctx = {"interpreter": interp, "backend": "oi"}
    out = list(ValidationMiddleware().process(_stream_writing(f, "def bad(:\n p"), ctx))

    assert not any("[Validation]" in (c.get("content") or "") for c in out)


def test_validation_disabled_passthrough(tmp_path):
    interp = _hermes_interp(tmp_path, enable_validation=False)
    f = tmp_path / "w.py"
    f.write_text("x = 1")

    ctx = {"interpreter": interp, "backend": "hermes"}
    out = list(ValidationMiddleware().process(_stream_writing(f, "def bad(:\n p"), ctx))

    assert not any("[Validation]" in (c.get("content") or "") for c in out)
