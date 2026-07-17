"""Outcome memory — success/failure tracking with causal attribution.

Learns from what actually happened: it reads execution results out of the
conversation (code blocks followed by console output), classifies each as
success or failure, and persists failures keyed by a normalized *error
signature* so recurring problems can be surfaced across sessions ("this error
has hit N times before").

Signal source: the message history (real console output), not ``Edit.result``
(which the recorder does not yet populate). When structured results land in the
edit graph later, they can feed the same store as a cleaner source.

Fully self-contained: no dependency on respond.py, the recorder, or the edit
graph. Non-blocking by construction — extraction/formatting never raise out.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Markers that identify a failed execution in console output.
_ERROR_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception):", re.M),
    re.compile(r"\bcommand not found\b"),
    re.compile(r"\bNo such file or directory\b"),
    re.compile(r"\bSyntaxError\b"),
    re.compile(r"^error:", re.I | re.M),
]
# Pull "ExceptionType: message" as the signature when present.
_EXC_LINE = re.compile(r"([A-Za-z_][\w.]*(?:Error|Exception): .+)")


def _is_failure(output: str) -> bool:
    return any(p.search(output) for p in _ERROR_PATTERNS)


def _signature(output: str) -> str:
    """A stable, cross-session key for an error — its exception line if any."""
    m = _EXC_LINE.search(output)
    if m:
        return m.group(1).strip()[:200]
    for line in output.splitlines():
        line = line.strip()
        if line and _is_failure(line):
            return line[:200]
    return output.strip()[:200]


@dataclass
class Outcome:
    signature: str  # normalized failure key (or "ok:<n>" for successes)
    status: str  # "success" | "failure"
    summary: str = ""  # the code/command that produced it
    error: str = ""  # short error excerpt
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


def _console_text(msg: dict) -> str:
    if not isinstance(msg, dict) or msg.get("type") != "console":
        return ""
    content = msg.get("content")
    return content if isinstance(content, str) else ""


def extract_outcomes(messages: list, start_index: int = 0) -> list[Outcome]:
    """Classify code executions in ``messages[start_index:]`` as success/failure.

    Pairs each assistant code block with the console output that follows it.
    Only failures carry a meaningful signature (successes are not persisted).
    """
    outcomes: list[Outcome] = []
    msgs = messages or []
    for i in range(max(0, start_index), len(msgs)):
        msg = msgs[i]
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "assistant" and msg.get("type") == "code":
            code = msg.get("content") or ""
            output = ""
            for j in range(i + 1, min(i + 4, len(msgs))):
                output += _console_text(msgs[j])
            if not output:
                continue
            if _is_failure(output):
                outcomes.append(
                    Outcome(
                        signature=_signature(output),
                        status="failure",
                        summary=str(code)[:200],
                        error=_signature(output),
                    )
                )
            else:
                outcomes.append(Outcome(signature="", status="success"))
    return outcomes


class OutcomeStore:
    """SQLite store of execution failures, aggregated by error signature."""

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
            CREATE TABLE IF NOT EXISTS outcomes (
                signature TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 1,
                summary TEXT,
                error TEXT,
                last_seen TEXT
            )
            """
        )
        self._conn.commit()

    def record(self, outcome: Outcome, now: str | None = None) -> None:
        """Persist a failure, incrementing its count. Successes are ignored."""
        if outcome.status != "failure" or not outcome.signature:
            return
        seen = now or datetime.now().isoformat()
        self._conn.execute(
            """
            INSERT INTO outcomes (signature, count, summary, error, last_seen)
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(signature) DO UPDATE SET
                count = count + 1,
                summary = excluded.summary,
                error = excluded.error,
                last_seen = excluded.last_seen
            """,
            (outcome.signature, outcome.summary, outcome.error, seen),
        )
        self._conn.commit()

    def record_from_messages(self, messages: list, start_index: int = 0) -> int:
        """Extract and persist failures from ``messages[start_index:]``.
        Returns the number of failures recorded."""
        n = 0
        for outcome in extract_outcomes(messages, start_index):
            if outcome.status == "failure":
                self.record(outcome)
                n += 1
        return n

    def recurring_failures(self, min_count: int = 1, limit: int = 3) -> list[dict]:
        rows = self._conn.execute(
            "SELECT signature, count, error FROM outcomes "
            "WHERE count >= ? ORDER BY count DESC, last_seen DESC LIMIT ?",
            (min_count, limit),
        ).fetchall()
        return [
            {"signature": r["signature"], "count": r["count"], "error": r["error"]}
            for r in rows
        ]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
