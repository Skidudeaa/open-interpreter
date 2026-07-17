"""Source-file change detection, extracted as a reusable service.

Wraps the filesystem snapshot/diff utilities (`utils.file_snapshot`) behind a
small object both backends can call. The built-in `respond()` loop brackets a
single code cell; the hermes MemoryMiddleware brackets the whole out-of-process
turn — same detection, different scope. This is *detection only* (shared infra
feeding memory, auto-test, and diff display); recording/commit live in
MemoryRecorder.

All methods are non-blocking: on any error they return an empty result so a
detection failure never disturbs the execution loop.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class FileChangeDetector:
    """Captures and diffs source-file states across an execution boundary."""

    def capture(self, cwd: str | None) -> dict:
        """Snapshot source-file states under ``cwd`` (defaults to '.'). Returns {}
        on error."""
        try:
            from ..utils.file_snapshot import capture_source_file_states

            return capture_source_file_states(cwd or ".")
        except Exception as e:
            logger.debug(f"File snapshot capture failed (non-blocking): {e}")
            return {}

    def diff(self, before: dict, after: dict) -> dict:
        """Return {path: (old_content, new_content)} for changed files. {} on error."""
        try:
            from ..utils.file_snapshot import diff_file_states

            return diff_file_states(before, after)
        except Exception as e:
            logger.debug(f"File diff failed (non-blocking): {e}")
            return {}

    def changes_since(self, before: dict, cwd: str | None) -> dict:
        """Capture the current state under ``cwd`` and diff it against ``before``.
        Empty dict when ``before`` is empty or on error."""
        if not before:
            return {}
        return self.diff(before, self.capture(cwd))
