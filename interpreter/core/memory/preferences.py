"""Preference memory — explicit user preference declarations.

Captures statements like "I prefer X", "always use Y", "never do Z", "avoid W"
from user messages, stores them durably, and exposes the active set for
injection into the system message (pre-prompting).

Two concerns:
- ``extract_preferences(text)`` — high-precision, rule-based detection of
  *explicit* declarations (no LLM; deterministic and testable).
- ``PreferenceStore`` — SQLite persistence with contradiction handling: a new
  preference that opposes an active one on the same subject deactivates the old
  (explicit > implicit; preferences decay when contradicted).
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Ordered (regex, polarity, confidence). First match wins per sentence. Patterns
# are deliberately narrow to keep precision high — a wrong preference is worse
# than a missed one, since it silently steers every future turn.
_PATTERNS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"\buse\s+(.+?)\s+instead of\s+(.+)", re.I), "prefer_over", 0.9),
    (re.compile(r"\bi\s+prefer\s+(.+)", re.I), "prefer", 0.9),
    (re.compile(r"\bi(?:'?d| would)\s+rather\s+(.+)", re.I), "prefer", 0.85),
    (re.compile(r"\bi\s+(?:don'?t|do not)\s+(?:like|want)\s+(.+)", re.I), "avoid", 0.9),
    (re.compile(r"\bi\s+(?:hate|dislike)\s+(.+)", re.I), "avoid", 0.85),
    (re.compile(r"\b(?:please\s+)?never\s+(.+)", re.I), "avoid", 0.85),
    (
        re.compile(r"\b(?:please\s+)?(?:always|make sure to)\s+(.+)", re.I),
        "prefer",
        0.85,
    ),
    (re.compile(r"\bavoid\s+(.+)", re.I), "avoid", 0.8),
    (re.compile(r"\bi\s+(?:like|love|want)\s+(.+)", re.I), "prefer", 0.6),
]

# Split into sentence-ish clauses so one message can hold several declarations.
_CLAUSE_SPLIT = re.compile(r"[.!?\n;]+|,?\s+(?:and|but)\s+", re.I)


def _normalize_subject(text: str) -> str:
    """A stable key for contradiction/reinforcement matching."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return " ".join(words)


@dataclass
class Preference:
    text: str  # human-readable statement, e.g. "prefer tabs over spaces"
    polarity: str  # "prefer" | "avoid"
    subject: str  # normalized key for matching
    source_text: str = ""  # the original clause it came from
    confidence: float = 0.8
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    active: bool = True

    def display(self) -> str:
        verb = "Prefer" if self.polarity == "prefer" else "Avoid"
        return f"{verb}: {self.text}"


def _clean(obj: str) -> str:
    return obj.strip().strip("\"'").rstrip(".!?,").strip()


def extract_preferences(text: str) -> list[Preference]:
    """Detect explicit preference declarations in ``text``. High precision."""
    if not text or not text.strip():
        return []
    found: list[Preference] = []
    seen_subjects: set[str] = set()
    for clause in _CLAUSE_SPLIT.split(text):
        clause = clause.strip()
        if len(clause) < 4:
            continue
        for pattern, polarity, conf in _PATTERNS:
            m = pattern.search(clause)
            if not m:
                continue
            if polarity == "prefer_over":
                liked, disliked = _clean(m.group(1)), _clean(m.group(2))
                pairs = [("prefer", liked), ("avoid", disliked)]
            else:
                pairs = [(polarity, _clean(m.group(1)))]
            for pol, obj in pairs:
                if len(obj) < 2:
                    continue
                subject = _normalize_subject(obj)
                if not subject or subject in seen_subjects:
                    continue
                seen_subjects.add(subject)
                verb = "prefer" if pol == "prefer" else "avoid"
                found.append(
                    Preference(
                        text=f"{verb} {obj}",
                        polarity=pol,
                        subject=subject,
                        source_text=clause,
                        confidence=conf,
                    )
                )
            break  # one declaration per clause
    return found


class PreferenceStore:
    """SQLite-backed durable store for user preferences."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path
        # check_same_thread=False: the store is read during system-message
        # assembly, which may run on a worker thread. Writes are low-frequency;
        # SQLite's own locking serializes them.
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
            CREATE TABLE IF NOT EXISTS preferences (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                polarity TEXT NOT NULL,
                subject TEXT NOT NULL,
                source_text TEXT,
                confidence REAL,
                created_at TEXT,
                active INTEGER DEFAULT 1
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pref_subject ON preferences(subject)"
        )
        self._conn.commit()

    def record(self, pref: Preference, now: str | None = None) -> str:
        """Store a preference with contradiction handling.

        - Opposite polarity on the same subject → deactivate the old (contradiction).
        - Same subject + polarity → reinforce (keep newest, drop the stale row).
        Returns the stored preference id.
        """
        created_at = now or datetime.now().isoformat()
        cur = self._conn.execute(
            "SELECT id, polarity FROM preferences WHERE subject = ? AND active = 1",
            (pref.subject,),
        )
        for row in cur.fetchall():
            if row["polarity"] != pref.polarity:
                # Contradiction — the newer explicit statement wins.
                self._conn.execute(
                    "UPDATE preferences SET active = 0 WHERE id = ?", (row["id"],)
                )
            else:
                # Reinforcement — supersede the older identical-polarity row.
                self._conn.execute("DELETE FROM preferences WHERE id = ?", (row["id"],))
        self._conn.execute(
            "INSERT INTO preferences (id, text, polarity, subject, source_text, "
            "confidence, created_at, active) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
            (
                pref.id,
                pref.text,
                pref.polarity,
                pref.subject,
                pref.source_text,
                pref.confidence,
                created_at,
            ),
        )
        self._conn.commit()
        return pref.id

    def record_from_text(self, text: str) -> list[Preference]:
        """Extract and store all explicit preferences found in ``text``."""
        prefs = extract_preferences(text)
        for pref in prefs:
            self.record(pref)
        return prefs

    def get_active(self, limit: int = 20) -> list[Preference]:
        rows = self._conn.execute(
            "SELECT * FROM preferences WHERE active = 1 "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Preference(
                id=r["id"],
                text=r["text"],
                polarity=r["polarity"],
                subject=r["subject"],
                source_text=r["source_text"] or "",
                confidence=r["confidence"] or 0.0,
                active=True,
            )
            for r in rows
        ]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
