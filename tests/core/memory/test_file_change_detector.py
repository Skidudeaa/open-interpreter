"""Unit tests for the FileChangeDetector service (Phase 2)."""

import tempfile
from pathlib import Path

from interpreter.core.memory.file_change_detector import FileChangeDetector


def test_changes_since_round_trips_a_real_edit():
    det = FileChangeDetector()
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "mod.py"
        f.write_text("# before")
        before = det.capture(tmp)
        assert str(f) in before

        f.write_text("# after")
        changed = det.changes_since(before, tmp)

        assert str(f) in changed
        assert changed[str(f)] == ("# before", "# after")


def test_changes_since_none_before_returns_empty():
    """None baseline = detection was not performed -> nothing to diff."""
    assert FileChangeDetector().changes_since(None, ".") == {}


def test_changes_since_empty_before_detects_new_file(tmp_path):
    """REGRESSION: an *empty* baseline ({}) is valid — a NEW file created in a
    previously-empty dir must be detected (was silently missed by an `if not
    before` guard, surfaced by a live hermes run in a fresh directory)."""
    det = FileChangeDetector()
    before = det.capture(str(tmp_path))  # empty dir -> {}
    assert before == {}

    new = tmp_path / "created.py"
    new.write_text("print('new')")

    changed = det.changes_since(before, str(tmp_path))
    assert str(new) in changed
    assert changed[str(new)] == ("", "print('new')")


def test_no_change_yields_empty_diff():
    det = FileChangeDetector()
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "a.py").write_text("x = 1")
        before = det.capture(tmp)
        assert det.changes_since(before, tmp) == {}


def test_capture_bad_path_is_non_blocking():
    # Nonexistent path -> capture returns a dict (possibly empty), never raises.
    assert isinstance(FileChangeDetector().capture("/no/such/dir/xyz"), dict)
