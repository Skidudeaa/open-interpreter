"""MemoryMiddleware — record file edits made during a turn into the memory graph.

For the built-in ``respond()`` loop this is a **pass-through**: respond() already
records edits inline per code cell. For the ``hermes`` backend (which executes
out-of-process and bypasses respond's hooks) this brackets the whole turn with a
before/after filesystem snapshot, diffs it via ``FileChangeDetector``, and feeds
the changes to ``MemoryRecorder`` + auto-commit — so **hermes edits land in the
SemanticEditGraph and light up FILE_CHANGE / MEMORY_RECORD / GIT_COMMIT events**
(the project's north star). See ``.planning/HERMES_CHUNKPIPELINE_PLAN.md`` Phase 3.

Snapshots bracket the *entire* hermes turn (coarser than oi's per-cell diff);
that granularity difference is intentional. Fully non-blocking: any failure is
logged at debug and never disturbs the stream.
"""

from __future__ import annotations

import logging

from ...terminal_interface.components.ui_events import EventType, UIEvent, get_event_bus
from ..memory.file_change_detector import FileChangeDetector
from ..memory.recorder import MemoryRecorder
from .base import Middleware

logger = logging.getLogger(__name__)


class MemoryMiddleware(Middleware):
    """Records file changes made during a hermes turn; no-op for the oi backend."""

    def process(self, chunks, ctx):
        if ctx.get("backend") != "hermes":
            # oi records edits inline in respond(); nothing to do here.
            yield from chunks
            return

        interpreter = ctx["interpreter"]
        cwd = getattr(getattr(interpreter, "computer", None), "cwd", None) or "."
        detector = FileChangeDetector()

        before = {}
        if getattr(interpreter, "enable_semantic_memory", False) or getattr(
            interpreter, "show_file_diffs", False
        ):
            before = detector.capture(cwd)

        try:
            yield from chunks
        finally:
            self._record_turn(interpreter, detector, before, cwd)

    def _record_turn(self, interpreter, detector, before, cwd):
        if not before:
            return
        try:
            changed = detector.changes_since(before, cwd)
            if not changed:
                return

            self._emit_file_changes(interpreter, changed)

            recorder = MemoryRecorder()
            user_msgs = [m for m in interpreter.messages if m.get("role") == "user"]
            edits = recorder.record_file_changes(
                interpreter,
                changed,
                user_msgs[-1].get("content", "") if user_msgs else "",
            )
            recorder.commit_edits(interpreter, edits)
        except Exception as e:  # pragma: no cover - defensive; must never break stream
            logger.debug(f"Hermes memory recording failed (non-blocking): {e}")

    @staticmethod
    def _emit_file_changes(interpreter, changed):
        if not getattr(interpreter, "show_file_diffs", False):
            return
        from ..respond import _detect_language

        event_bus = get_event_bus()
        for file_path, (old_content, new_content) in changed.items():
            event_bus.emit(
                UIEvent(
                    type=EventType.FILE_CHANGE,
                    data={
                        "file_path": file_path,
                        "old_content": old_content,
                        "new_content": new_content,
                        "language": _detect_language(file_path),
                    },
                    source="hermes",
                )
            )
