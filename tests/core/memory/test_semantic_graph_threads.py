"""Regression: SemanticEditGraph writes must work across threads.

The hermes memory pipeline records edits from the consumer thread while the
graph's connection may have been opened on another thread. SQLite objects are
thread-bound by default, so this used to raise sqlite3.ProgrammingError (swallowed
by the non-blocking recorder -> silent data loss). The connection is now opened
with check_same_thread=False and writes serialized by a lock.
"""

import tempfile
import threading
from pathlib import Path

from interpreter.core.core import _get_memory_module


def _graph(tmp):
    SG = _get_memory_module()["SemanticEditGraph"]
    return SG(db_path=str(Path(tmp) / "g.db"))


def _make_edit(path, content="print(1)"):
    cef = _get_memory_module()["create_edit_from_file_change"]
    return cef(
        file_path=path, original_content="", new_content=content, user_message="m"
    )


def test_record_edit_from_another_thread():
    with tempfile.TemporaryDirectory() as tmp:
        g = _graph(tmp)  # connection opened on this (main) thread
        before = g.get_statistics()["total_edits"]

        err = {}

        def worker():
            try:
                g.record_edit(_make_edit("/x/a.py"))
            except Exception as e:  # noqa
                err["e"] = repr(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert "e" not in err, f"cross-thread record raised: {err.get('e')}"
        assert g.get_statistics()["total_edits"] == before + 1


def test_concurrent_writes_all_land():
    with tempfile.TemporaryDirectory() as tmp:
        g = _graph(tmp)
        before = g.get_statistics()["total_edits"]

        def worker(n):
            g.record_edit(_make_edit(f"/x/f{n}.py", f"print({n})"))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert g.get_statistics()["total_edits"] == before + 10
