"""ValidationMiddleware — post-hoc syntax validation of hermes edits (warn-only).

The oi backend gates code *before* execution (respond()'s CodeGate). Hermes runs
out-of-process, so we can't pre-gate; instead we validate the *result* — after the
turn, syntax-check each file hermes changed and, on failure, inject a
``[Validation]`` console chunk. **Warn-only**: execution already happened and the
edit is left in place (no git rollback in this phase — see the plan's kill
criterion). No-op for oi and when ``enable_validation`` is off.

Because it validates the changed *files*, it runs its own before/after snapshot
(independent of MemoryMiddleware) so either can be enabled alone. Non-blocking.
"""

from __future__ import annotations

import logging

from ...terminal_interface.components.ui_events import EventType, UIEvent, get_event_bus
from ..memory.file_change_detector import FileChangeDetector
from .base import Middleware

logger = logging.getLogger(__name__)


class ValidationMiddleware(Middleware):
    """Post-hoc, warn-only syntax validation of hermes file edits."""

    def process(self, chunks, ctx):
        interpreter = ctx["interpreter"]
        if ctx.get("backend") != "hermes" or not getattr(
            interpreter, "enable_validation", False
        ):
            yield from chunks
            return

        cwd = getattr(getattr(interpreter, "computer", None), "cwd", None) or "."
        detector = FileChangeDetector()
        before = detector.capture(cwd)

        yield from chunks

        # Post-hoc: validate what changed, warn (don't block/rollback).
        yield from self._validate_changes(interpreter, detector, before, cwd)

    def _validate_changes(self, interpreter, detector, before, cwd):
        try:
            changed = detector.changes_since(before, cwd)
            checker = getattr(interpreter, "syntax_checker", None)
            if not (changed and checker):
                return

            event_bus = get_event_bus()
            event_bus.emit(
                UIEvent(
                    type=EventType.VALIDATION_START,
                    data={"files": len(changed)},
                    source="hermes",
                )
            )

            error_count = 0
            warnings = []
            for file_path, (_old, new_content) in changed.items():
                try:
                    result = checker.check(new_content, file_path)
                except Exception as e:
                    logger.debug(f"Syntax check failed (non-blocking): {e}")
                    continue
                if not result.valid:
                    for err in result.errors:
                        error_count += 1
                        warnings.append(f"[Validation] {file_path}: {err}\n")

            event_bus.emit(
                UIEvent(
                    type=EventType.VALIDATION_END,
                    data={"valid": error_count == 0, "error_count": error_count},
                    source="hermes",
                )
            )
        except Exception as e:  # pragma: no cover - defensive; must never break stream
            logger.debug(f"Hermes validation failed (non-blocking): {e}")
            return

        for line in warnings:
            yield {
                "role": "computer",
                "type": "console",
                "format": "output",
                "content": line,
            }
