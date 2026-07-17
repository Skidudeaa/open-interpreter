"""Context-pattern memory — behavioral inference from when/how you work.

Observes ``(time-of-day bucket, activity)`` for each turn and, once a pattern
has enough support, surfaces it ("it's late night — you usually debug around
this time"). This is the most speculative memory type, so it is deliberately
conservative: a hint fires only when a bucket has a *dominant* activity backed
by a minimum number of observations AND a majority share. Below that bar it
stays silent — evidence gates the claim.

Activity is inferred from the user's message by keyword (no LLM). The clock is
injectable (``_now``) so behavior is deterministic under test. Self-contained:
no respond.py / recorder / edit-graph deps.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path

# Ordered activity categories; first keyword hit wins. "other" is not recorded.
_ACTIVITY_KEYWORDS: list[tuple[str, list[str]]] = [
    (
        "debug",
        ["debug", "bug", "error", "traceback", "crash", "broken", "failing", "fix"],
    ),
    ("test", ["test", "pytest", "coverage", "assert", "unittest"]),
    (
        "refactor",
        [
            "refactor",
            "clean up",
            "cleanup",
            "simplify",
            "rename",
            "extract",
            "restructure",
        ],
    ),
    ("docs", ["document", "readme", "docstring", "comment", "changelog"]),
    (
        "research",
        [
            "research",
            "investigate",
            "explore",
            "look up",
            "how does",
            "what is",
            "find out",
        ],
    ),
    ("build", ["build", "implement", "add", "create", "feature", "wire", "write"]),
]


def _now() -> datetime:
    return datetime.now()


def classify_activity(text: str) -> str:
    if not text:
        return "other"
    low = text.lower()
    for activity, keywords in _ACTIVITY_KEYWORDS:
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw), low):
                return activity
    return "other"


def time_bucket(dt: datetime) -> str:
    h = dt.hour
    if h < 5:
        return "late night"
    if h < 9:
        return "early morning"
    if h < 12:
        return "morning"
    if h < 17:
        return "afternoon"
    if h < 21:
        return "evening"
    return "night"


class ContextPatternStore:
    """SQLite tally of (time bucket, activity) observations."""

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
            CREATE TABLE IF NOT EXISTS context_patterns (
                bucket TEXT NOT NULL,
                activity TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                last_seen TEXT,
                PRIMARY KEY (bucket, activity)
            )
            """
        )
        self._conn.commit()

    def record(self, bucket: str, activity: str, now: datetime | None = None) -> None:
        if activity == "other" or not bucket:
            return
        seen = (now or _now()).isoformat()
        self._conn.execute(
            """
            INSERT INTO context_patterns (bucket, activity, count, last_seen)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(bucket, activity) DO UPDATE SET
                count = count + 1, last_seen = excluded.last_seen
            """,
            (bucket, activity, seen),
        )
        self._conn.commit()

    def dominant(
        self, bucket: str, min_count: int = 3, min_share: float = 0.5
    ) -> dict | None:
        """The dominant activity for a bucket, or None if no pattern is strong
        enough (evidence gate: needs min_count observations and a majority)."""
        rows = self._conn.execute(
            "SELECT activity, count FROM context_patterns WHERE bucket = ?",
            (bucket,),
        ).fetchall()
        if not rows:
            return None
        total = sum(r["count"] for r in rows)
        top = max(rows, key=lambda r: r["count"])
        if top["count"] < min_count or total == 0:
            return None
        share = top["count"] / total
        if share < min_share:
            return None
        return {"activity": top["activity"], "count": top["count"], "share": share}

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
