"""System-message assembly, extracted from respond.py as a reusable service.

``SystemMessageBuilder.build(interpreter)`` returns the assembled (un-rendered)
system message string. It is pure logic with a process-global, per-interpreter
cache — no yielding, no message mutation — so both the built-in ``respond()``
loop and the ``hermes`` backend can call it to obtain OI's system prompt
(including source-routing) rather than reimplementing assembly.

The ``render_message()`` *injection* step stays at each call site — only the
*assembly* is shared here.
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass
from typing import Any

# System message cache to avoid rebuilding every iteration. Keyed by id(interpreter);
# kept module-global (not instance state) so cache lifetime is identical to the
# original inline implementation regardless of how many builders are created.


@dataclass(slots=True)
class _SysMsgCacheEntry:
    key: tuple[Any, ...]
    value: str
    interp_ref: weakref.ref | None = None


_system_message_cache: dict[int, _SysMsgCacheEntry] = {}

# Headless detection is stable per-process. Don't redo expensive/fragile checks
# every cache miss.
_IS_HEADLESS: bool | None = None


def _detect_headless() -> bool:
    global _IS_HEADLESS
    if _IS_HEADLESS is not None:
        return _IS_HEADLESS
    try:
        import pyautogui

        pyautogui.size()  # fails in headless
        _IS_HEADLESS = False
    except Exception:
        _IS_HEADLESS = True
    return _IS_HEADLESS


def _weakref_or_none(obj: Any) -> weakref.ref | None:
    """Best-effort weakref to protect against id() reuse after GC."""
    try:
        return weakref.ref(obj)
    except TypeError:
        return None


class SystemMessageBuilder:
    """Assembles the system message string for an interpreter, with caching."""

    def build(self, interpreter) -> str:
        """
        Build the system message: cached static base + an optional dynamic
        memory preamble (relevant past memories, reranked) injected per-turn.
        The static base keeps its dependency cache; the preamble is recomputed
        each call (it depends on the current user query) and is empty unless
        ``enable_memory_preprompt`` is on with semantic memory available.
        """
        static = self._build_static(interpreter)
        preamble = self._build_memory_preamble(interpreter)
        return f"{preamble}\n\n{static}" if preamble else static

    def _build_static(self, interpreter) -> str:
        """
        Build the static system message with caching based on dependencies.
        Returns cached version if dependencies haven't changed.
        """
        # Build cache key from dependencies (exclude id; we store per-interpreter id already)
        lang_messages = tuple(
            getattr(lang, "system_message", "")
            for lang in interpreter.computer.terminal.languages
            if hasattr(lang, "system_message")
        )
        cache_key = (
            interpreter.system_message,
            lang_messages,
            interpreter.custom_instructions,
            interpreter.computer.import_computer_api,
            (
                interpreter.computer.system_message
                if interpreter.computer.import_computer_api
                else ""
            ),
            _detect_headless(),
        )

        interpreter_id = id(interpreter)
        entry = _system_message_cache.get(interpreter_id)
        if entry is not None and entry.key == cache_key:
            if entry.interp_ref is None or entry.interp_ref() is interpreter:
                return entry.value
            # id() reused after GC
            _system_message_cache.pop(interpreter_id, None)

        # Build system message using parts (faster, avoids quadratic string appends)
        parts: list[str] = []
        base = getattr(interpreter, "system_message", "") or ""
        parts.append(base)

        for lang_msg in lang_messages:
            if lang_msg:
                parts.append(lang_msg)

        if interpreter.custom_instructions:
            parts.append(interpreter.custom_instructions)

        if (
            interpreter.computer.import_computer_api
            and interpreter.computer.system_message
        ):
            # Avoid duplicates by equality, not substring containment.
            if interpreter.computer.system_message not in parts:
                parts.append(interpreter.computer.system_message)

        if _detect_headless():
            parts.append(
                "IMPORTANT: This is a HEADLESS environment (no X11/display). "
                "Do NOT call computer.display.view(), computer.screenshot(), "
                "computer.mouse, computer.keyboard, or any GUI functions - they will fail."
            )

        system_message = "\n\n".join(p for p in parts if p)

        # Cache (bounded)
        _system_message_cache[interpreter_id] = _SysMsgCacheEntry(
            key=cache_key,
            value=system_message,
            interp_ref=_weakref_or_none(interpreter),
        )
        if len(_system_message_cache) > 128:
            _system_message_cache.clear()

        return system_message

    @staticmethod
    def _latest_user_query(interpreter) -> str:
        """The most recent user text message — the query to retrieve memories for."""
        for msg in reversed(getattr(interpreter, "messages", []) or []):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") == "user" and msg.get("type", "message") == "message":
                content = msg.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return " ".join(str(x) for x in content)
        return ""

    def _build_memory_preamble(self, interpreter) -> str:
        """Assemble the dynamic system-message preamble: stated preferences +
        relevant past memories. Each section is independently guarded and
        non-blocking, so the base system message is never affected."""
        sections = [
            self._preferences_section(interpreter),
            self._tasks_section(interpreter),
            self._outcome_section(interpreter),
            self._context_section(interpreter),
            self._edit_memory_section(interpreter),
        ]
        return "\n\n".join(s for s in sections if s)

    def _context_section(self, interpreter) -> str:
        """Observe (time-of-day, activity) for this turn and, once a pattern has
        enough support, surface it. Store lazily attached to the interpreter (no
        core.py dependency); capture deduped per message. Non-blocking."""
        if not getattr(interpreter, "enable_context_memory", False):
            return ""
        try:
            from ...terminal_interface.utils.local_storage_path import get_storage_path
            from ..memory import context_patterns as cp

            store = getattr(interpreter, "_context_store", None)
            if store is None:
                store = cp.ContextPatternStore(
                    db_path=get_storage_path("context_patterns.db")
                )
                interpreter._context_store = store
            now = cp._now()
            bucket = cp.time_bucket(now)
            query = self._latest_user_query(interpreter)
            activity = cp.classify_activity(query) if query else "other"
            if (
                activity != "other"
                and query
                and getattr(interpreter, "_last_context_capture", None) != query
            ):
                interpreter._last_context_capture = query
                store.record(bucket, activity, now=now)
            dom = store.dominant(bucket)
            if not dom:
                return ""
            return (
                "## Working context\n"
                f"It's {bucket} — you usually work on {dom['activity']} tasks "
                f"around this time (seen {dom['count']}x)."
            )
        except Exception:  # context memory must never break the system message
            return ""

    def _tasks_section(self, interpreter) -> str:
        """Capture task open/complete declarations from the current message and
        inject the open set for continuity. Store lazily attached to the
        interpreter (no core.py dependency); capture deduped per message."""
        if not getattr(interpreter, "enable_task_memory", False):
            return ""
        try:
            store = getattr(interpreter, "_task_store", None)
            if store is None:
                from ...terminal_interface.utils.local_storage_path import (
                    get_storage_path,
                )
                from ..memory.tasks import TaskStore

                store = TaskStore(db_path=get_storage_path("tasks.db"))
                interpreter._task_store = store
            query = self._latest_user_query(interpreter)
            if query and getattr(interpreter, "_last_task_capture", None) != query:
                interpreter._last_task_capture = query
                store.record_from_text(query)
            open_tasks = store.get_open(limit=getattr(interpreter, "task_limit", 10))
            if not open_tasks:
                return ""
            lines = [f"- {t.title}" for t in open_tasks]
            return (
                "## Open tasks\n"
                "Work in progress — keep these in mind:\n" + "\n".join(lines)
            )
        except Exception:  # task memory must never break the system message
            return ""

    def _outcome_section(self, interpreter) -> str:
        """Record execution failures from the conversation and inject a warning
        about ones seen before. The store is lazily attached to the interpreter
        (avoids a core.py dependency) and scanning is incremental. Non-blocking."""
        if not getattr(interpreter, "enable_outcome_memory", False):
            return ""
        try:
            store = getattr(interpreter, "_outcome_store", None)
            if store is None:
                from ...terminal_interface.utils.local_storage_path import (
                    get_storage_path,
                )
                from ..memory.outcomes import OutcomeStore

                store = OutcomeStore(db_path=get_storage_path("outcomes.db"))
                interpreter._outcome_store = store
            messages = getattr(interpreter, "messages", []) or []
            scanned = getattr(interpreter, "_last_outcome_scan", 0)
            if len(messages) > scanned:
                store.record_from_messages(messages, start_index=scanned)
                interpreter._last_outcome_scan = len(messages)
            recurring = store.recurring_failures(min_count=1, limit=3)
            if not recurring:
                return ""
            lines = []
            for r in recurring:
                times = "once" if r["count"] == 1 else f"{r['count']}x"
                lines.append(f"- {r['signature']} (seen {times})")
            return (
                "## Past failures to avoid repeating\n"
                "These errors have occurred before:\n" + "\n".join(lines)
            )
        except Exception:  # outcome memory must never break the system message
            return ""

    def _preferences_section(self, interpreter) -> str:
        """Capture explicit preferences from the current message and inject the
        active set. Capture happens here (before the LLM sees the turn) and is
        deduplicated per message; both are fully non-blocking."""
        if not getattr(interpreter, "enable_preference_memory", False):
            return ""
        try:
            store = getattr(interpreter, "preference_store", None)
            if store is None:
                return ""
            query = self._latest_user_query(interpreter)
            # Capture once per unique message (build() may run several times/turn).
            if query and getattr(interpreter, "_last_pref_capture", None) != query:
                interpreter._last_pref_capture = query
                store.record_from_text(query)
            active = store.get_active(
                limit=getattr(interpreter, "preference_limit", 20)
            )
            if not active:
                return ""
            lines = [f"- {p.display()}" for p in active]
            return (
                "## User preferences\n"
                "Honor these stated preferences:\n" + "\n".join(lines)
            )
        except Exception:  # preference memory must never break the system message
            return ""

    def _edit_memory_section(self, interpreter) -> str:
        """Retrieve relevant past memories for the current query and format them.
        Fully non-blocking: returns '' when disabled, unavailable, empty, or on
        any error."""
        if not getattr(interpreter, "enable_memory_preprompt", False):
            return ""
        try:
            graph = getattr(interpreter, "semantic_graph", None)
            if graph is None or not hasattr(graph, "semantic_search"):
                return ""
            query = self._latest_user_query(interpreter)
            if not query:
                return ""
            results = graph.semantic_search(
                query,
                limit=getattr(interpreter, "memory_preprompt_limit", 5),
                reranker=getattr(interpreter, "reranker", None),
            )
            lines: list[str] = []
            for r in results:
                content = (r.get("content") or "").strip()
                if not content:
                    continue
                path = (r.get("metadata") or {}).get("file_path")
                lines.append(f"- {content}" + (f" ({path})" if path else ""))
            if not lines:
                return ""
            return (
                "## Relevant context from past work\n"
                "These past memories may relate to the current request:\n"
                + "\n".join(lines)
            )
        except Exception:  # pre-prompting must never break the system message
            return ""
