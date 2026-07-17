"""Semantic-memory recording, extracted from respond.py as a reusable service.

``MemoryRecorder`` turns executed code and detected file changes into
``SemanticEditGraph`` records. It is pure logic (no generator/yield) so both the
built-in ``respond()`` loop and the ``hermes`` backend can feed edits into the
memory graph — the project's north star ("the system learns YOU"). See
``.planning/HERMES_COMPLEMENTARY_PLAN.md`` Phase 2.

Each method keeps its own non-blocking ``try/except -> logger.debug -> default``
contract (memory errors must never crash a turn) and emits its own UI events, so
callers stay thin and the guards live in one place.

Note: file-change *detection* (snapshot capture/diff + FILE_CHANGE display
events) is deliberately NOT owned here — it is shared infra that feeds
``changed_files`` to memory, auto-test, and diff display alike. Callers pass
``changed_files`` in.
"""

from __future__ import annotations

import logging

from ...terminal_interface.components.ui_events import EventType, UIEvent, get_event_bus

logger = logging.getLogger(__name__)


class MemoryRecorder:
    """Records code executions and file-change edits into the semantic graph."""

    def record_code_execution(self, interpreter, code: str, language: str) -> bool:
        """Record an executed code block as an Edit. Returns True if recorded
        (drives the '✓ recorded' status flag)."""
        if not (interpreter.enable_semantic_memory and interpreter.semantic_graph):
            return False
        try:
            from ..core import _get_memory_module

            memory_module = _get_memory_module()
            Edit = memory_module["Edit"]
            EditType = memory_module["EditType"]

            # Get conversation context
            context = None
            if interpreter.conversation_linker:
                user_msgs = [m for m in interpreter.messages if m.get("role") == "user"]
                if user_msgs:
                    context = interpreter.conversation_linker.create_context(
                        user_message=user_msgs[-1].get("content", ""),
                        assistant_response=code,
                    )

            # Record the code execution. NOTE: the original inline code was doubly
            # broken and silently no-op'd (its AttributeError/TypeError was swallowed
            # by the non-blocking except): it passed edit_type=EditType.OTHER (absent
            # from the enum) and language=... (Edit has no `language` field). Build it
            # with valid fields — EditType.UNKNOWN (uncategorized script execution) and
            # the language captured in user_intent.
            edit = Edit(
                file_path="",  # Script execution, not a file edit
                original_content="",
                new_content=code,
                edit_type=EditType.UNKNOWN,
                user_intent=f"Executed {language} code",
                conversation_context=context,
            )
            interpreter.semantic_graph.record_edit(edit)

            # Emit memory record event for UI feedback
            event_bus = get_event_bus()
            event_bus.emit(
                UIEvent(
                    type=EventType.MEMORY_RECORD,
                    data={"type": "code_execution", "language": language},
                    source="respond",
                )
            )
            return True
        except Exception as e:
            logger.debug(f"Semantic memory recording failed (non-blocking): {e}")
            return False

    def record_file_changes(
        self, interpreter, changed_files: dict, user_message: str
    ) -> list:
        """Record detected file changes as Edits. Returns the list of edits (for
        auto-commit). Empty list when disabled or on error."""
        if not (
            changed_files
            and interpreter.enable_semantic_memory
            and interpreter.semantic_graph
        ):
            return []
        try:
            from ..core import _get_memory_module

            memory_module = _get_memory_module()
            create_edit = memory_module.get("create_edit_from_file_change")

            # Collect all edits for batch processing
            edits_to_commit = []

            for file_path, (old_content, new_content) in changed_files.items():
                if create_edit:
                    edit = create_edit(
                        file_path=file_path,
                        original_content=old_content,
                        new_content=new_content,
                        user_message=user_message,
                    )
                    interpreter.semantic_graph.record_edit(edit)
                    edits_to_commit.append(edit)

            return edits_to_commit
        except Exception as e:
            logger.debug(f"File-change memory recording failed (non-blocking): {e}")
            return []

    def commit_edits(self, interpreter, edits: list) -> str | None:
        """Auto-commit recorded edits if enabled. Returns the commit hash (drives
        the '✓ committed' status flag) or None."""
        if not (interpreter.auto_commit and edits):
            return None
        try:
            from ..validation.auto_commit import batch_auto_commit

            commit_hash = batch_auto_commit(
                edits=edits,
                project_root=interpreter.computer.cwd or ".",
            )

            if commit_hash:
                # Update all edits with the commit hash
                for edit in edits:
                    edit.git_commit_hash = commit_hash
                    interpreter.semantic_graph.update_edit_commit_hash(
                        edit.id, commit_hash
                    )

                # Emit commit event for UI feedback
                event_bus = get_event_bus()
                event_bus.emit(
                    UIEvent(
                        type=EventType.GIT_COMMIT,
                        data={
                            "commit_hash": commit_hash,
                            "files_count": len(edits),
                        },
                        source="respond",
                    )
                )
            return commit_hash
        except Exception as commit_error:
            logger.debug(f"Auto-commit failed (non-blocking): {commit_error}")
            return None
