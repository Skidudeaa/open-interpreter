"""Task-state memory — track what the user is working on across turns/sessions.

Detects explicit task declarations ("let's X", "TODO: X", "we need to X") and
completions ("done with X", "X is finished") in user messages, stores them with
status, and exposes the open set for injection into the system message so the
model keeps continuity on in-flight work.

v1 is flat (no hierarchy inference — too noisy from freeform text); the schema
carries a ``parent_id`` for a future project→task tree. High-precision,
rule-based, no LLM. Self-contained: no respond.py / recorder / edit-graph deps.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Declarations that open a task. Capture the object clause.
_OPEN_PATTERNS = [
    re.compile(r"\b(?:todo|task)\s*[:\-]\s*(.+)", re.I),
    re.compile(r"\blet'?s\s+(.+)", re.I),
    re.compile(r"\b(?:i|we)\s+(?:need to|want to|have to|should|gotta)\s+(.+)", re.I),
    re.compile(r"\bnext(?:\s+up)?\s*[:,]\s*(?:let'?s\s+)?(.+)", re.I),
]
# Declarations that close a task.
_DONE_PATTERNS = [
    re.compile(r"\b(?:done|finished|completed)\s+(?:with\s+)?(.+)", re.I),
    re.compile(r"\b(.+?)\s+is\s+(?:done|finished|complete|working)\b", re.I),
]
_CLAUSE_SPLIT = re.compile(r"[.!?\n;]+", re.I)


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _clean(obj: str) -> str:
    return obj.strip().strip("\"'").rstrip(".!?,").strip()


@dataclass
class TaskEvent:
    kind: str  # "open" | "done"
    title: str
    subject: str


def extract_task_events(text: str) -> list[TaskEvent]:
    """Detect task open/complete declarations in ``text``. High precision."""
    if not text or not text.strip():
        return []
    events: list[TaskEvent] = []
    seen: set[tuple[str, str]] = set()
    for clause in _CLAUSE_SPLIT.split(text):
        clause = clause.strip()
        if len(clause) < 5:
            continue
        matched = False
        # Completions first — "done with X" shouldn't also register as an open.
        for pattern in _DONE_PATTERNS:
            m = pattern.search(clause)
            if m:
                obj = _clean(m.group(1))
                subject = _normalize(obj)
                if len(obj) >= 3 and subject and ("done", subject) not in seen:
                    seen.add(("done", subject))
                    events.append(TaskEvent("done", obj, subject))
                matched = True
                break
        if matched:
            continue
        for pattern in _OPEN_PATTERNS:
            m = pattern.search(clause)
            if m:
                obj = _clean(m.group(1))
                subject = _normalize(obj)
                # Require a couple of words so "let's go" doesn't become a task.
                if len(subject.split()) >= 2 and ("open", subject) not in seen:
                    seen.add(("open", subject))
                    events.append(TaskEvent("open", obj, subject))
                break
    return events


@dataclass
class Task:
    title: str
    subject: str
    status: str = "open"  # "open" | "done"
    parent_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class TaskStore:
    """SQLite store of tasks with open/done status."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
        else:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                subject TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                parent_id TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_subject ON tasks(subject)"
        )
        self._conn.commit()

    def open_task(self, task: Task, now: str | None = None) -> str:
        """Open a task. Re-opening an existing subject just refreshes it (no dup)."""
        ts = now or datetime.now().isoformat()
        existing = self._conn.execute(
            "SELECT id FROM tasks WHERE subject = ? AND status = 'open'",
            (task.subject,),
        ).fetchone()
        if existing:
            self._conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?", (ts, existing["id"])
            )
            self._conn.commit()
            return existing["id"]
        self._conn.execute(
            "INSERT INTO tasks (id, title, subject, status, parent_id, created_at, "
            "updated_at) VALUES (?, ?, ?, 'open', ?, ?, ?)",
            (task.id, task.title, task.subject, task.parent_id, ts, ts),
        )
        self._conn.commit()
        return task.id

    def complete_by_subject(self, subject: str, now: str | None = None) -> int:
        """Mark open tasks whose subject overlaps ``subject`` as done.
        Returns the number completed."""
        ts = now or datetime.now().isoformat()
        target = set(subject.split())
        completed = 0
        for row in self._conn.execute(
            "SELECT id, subject FROM tasks WHERE status = 'open'"
        ).fetchall():
            words = set(row["subject"].split())
            # Overlap heuristic: the shorter side is mostly contained in the other.
            if words and target and len(words & target) >= min(len(words), len(target)):
                self._conn.execute(
                    "UPDATE tasks SET status = 'done', updated_at = ? WHERE id = ?",
                    (ts, row["id"]),
                )
                completed += 1
        self._conn.commit()
        return completed

    def apply_events(self, events: list[TaskEvent]) -> None:
        for ev in events:
            if ev.kind == "open":
                self.open_task(Task(title=ev.title, subject=ev.subject))
            else:
                self.complete_by_subject(ev.subject)

    def record_from_text(self, text: str) -> list[TaskEvent]:
        events = extract_task_events(text)
        self.apply_events(events)
        return events

    def get_open(self, limit: int = 10) -> list[Task]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE status = 'open' "
            "ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Task(
                id=r["id"],
                title=r["title"],
                subject=r["subject"],
                status=r["status"],
                parent_id=r["parent_id"],
            )
            for r in rows
        ]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
