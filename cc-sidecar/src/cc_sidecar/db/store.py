"""
EventStore — SQLite database access layer for cc-sidecar.

Handles:
    - Schema initialization with WAL mode
    - Raw event insertion with hash-based dedup
    - Upsert operations for materialized state tables
    - Query methods for UI panels
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .schema import SCHEMA_VERSION, get_schema_sql

# Default database location
DEFAULT_DB_DIR = Path.home() / ".cc-sidecar"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "sidecar.db"


class EventStore:
    """Thread-safe SQLite store for sidecar events and state."""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database with schema."""
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level="DEFERRED",
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(get_schema_sql())
        # Store schema version
        self._conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # --- Raw events ---

    @staticmethod
    def _hash_payload(payload_json: str) -> str:
        """Compute SHA-256 hash of payload for dedup."""
        return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    def insert_raw_event(
        self,
        received_at_ms: int,
        seq: int,
        session_id: str,
        source_kind: str,
        event_name: str,
        payload: dict[str, Any],
    ) -> int | None:
        """Insert a raw event. Returns row id, or None if duplicate."""
        payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        payload_hash = self._hash_payload(payload_json)
        with self._lock:
            try:
                cur = self._conn.execute(
                    """INSERT INTO raw_events
                       (received_at_ms, seq, session_id, source_kind, event_name, payload_json, payload_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (received_at_ms, seq, session_id, source_kind, event_name, payload_json, payload_hash),
                )
                self._conn.commit()
                return cur.lastrowid
            except sqlite3.IntegrityError:
                # Duplicate payload_hash — idempotent
                return None

    # --- Sessions ---

    def upsert_session(self, session_id: str, **fields: Any) -> None:
        """Upsert session state. Only provided fields are updated."""
        with self._lock:
            existing = self._conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if existing:
                if fields:
                    set_clause = ", ".join(f"{k} = ?" for k in fields)
                    self._conn.execute(
                        f"UPDATE sessions SET {set_clause} WHERE session_id = ?",
                        (*fields.values(), session_id),
                    )
            else:
                cols = ["session_id"] + list(fields.keys())
                placeholders = ", ".join("?" for _ in cols)
                col_names = ", ".join(cols)
                self._conn.execute(
                    f"INSERT INTO sessions ({col_names}) VALUES ({placeholders})",
                    (session_id, *fields.values()),
                )
            self._conn.commit()

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Get session by id."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent sessions."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY last_seen_at_ms DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Agents ---

    def upsert_agent(self, agent_pk: str, session_id: str, **fields: Any) -> None:
        """Upsert agent state."""
        with self._lock:
            existing = self._conn.execute(
                "SELECT 1 FROM agents WHERE agent_pk = ?", (agent_pk,)
            ).fetchone()
            if existing:
                if fields:
                    set_clause = ", ".join(f"{k} = ?" for k in fields)
                    self._conn.execute(
                        f"UPDATE agents SET {set_clause} WHERE agent_pk = ?",
                        (*fields.values(), agent_pk),
                    )
            else:
                all_fields = {"agent_pk": agent_pk, "session_id": session_id, **fields}
                # Ensure required defaults
                all_fields.setdefault("agent_type", "unknown")
                all_fields.setdefault("state", "idle")
                all_fields.setdefault("state_source", "observed")
                all_fields.setdefault("visibility_mode", "lifecycle_only")
                cols = ", ".join(all_fields.keys())
                placeholders = ", ".join("?" for _ in all_fields)
                self._conn.execute(
                    f"INSERT INTO agents ({cols}) VALUES ({placeholders})",
                    tuple(all_fields.values()),
                )
            self._conn.commit()

    def get_agents(self, session_id: str) -> list[dict[str, Any]]:
        """Get all agents for a session."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agents WHERE session_id = ? ORDER BY started_at_ms",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_active_agents(self, session_id: str) -> list[dict[str, Any]]:
        """Get agents that are not in a terminal state."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM agents
                   WHERE session_id = ?
                     AND state NOT IN ('finished', 'finished_warn', 'finished_error', 'orphaned')
                   ORDER BY started_at_ms""",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Tool calls ---

    def insert_tool_call(
        self,
        tool_use_id: str,
        session_id: str,
        agent_pk: str,
        tool_name: str,
        started_at_ms: int,
        input_preview: str | None = None,
    ) -> None:
        """Insert a new tool call (started)."""
        with self._lock:
            self._conn.execute(
                """INSERT OR IGNORE INTO tool_calls
                   (tool_use_id, session_id, agent_pk, tool_name, status, started_at_ms, input_preview)
                   VALUES (?, ?, ?, ?, 'started', ?, ?)""",
                (tool_use_id, session_id, agent_pk, tool_name, started_at_ms, input_preview),
            )
            self._conn.commit()

    def close_tool_call(
        self,
        tool_use_id: str,
        status: str,
        ended_at_ms: int,
        output_preview: str | None = None,
        error: str | None = None,
    ) -> None:
        """Close a tool call with final status."""
        with self._lock:
            self._conn.execute(
                """UPDATE tool_calls
                   SET status = ?, ended_at_ms = ?, output_preview = ?, error = ?
                   WHERE tool_use_id = ?""",
                (status, ended_at_ms, output_preview, error, tool_use_id),
            )
            self._conn.commit()

    def get_recent_tool_calls(
        self, session_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get recent tool calls for a session."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM tool_calls
                   WHERE session_id = ?
                   ORDER BY started_at_ms DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Files ---

    def upsert_file(self, session_id: str, path: str, **fields: Any) -> None:
        """Upsert file change record."""
        with self._lock:
            existing = self._conn.execute(
                "SELECT 1 FROM files WHERE session_id = ? AND path = ?",
                (session_id, path),
            ).fetchone()
            if existing:
                if fields:
                    set_clause = ", ".join(f"{k} = ?" for k in fields)
                    self._conn.execute(
                        f"UPDATE files SET {set_clause} WHERE session_id = ? AND path = ?",
                        (*fields.values(), session_id, path),
                    )
            else:
                all_fields = {"session_id": session_id, "path": path, **fields}
                all_fields.setdefault("ownership_source", "unknown")
                cols = ", ".join(all_fields.keys())
                placeholders = ", ".join("?" for _ in all_fields)
                self._conn.execute(
                    f"INSERT INTO files ({cols}) VALUES ({placeholders})",
                    tuple(all_fields.values()),
                )
            self._conn.commit()

    def get_files(self, session_id: str) -> list[dict[str, Any]]:
        """Get changed files for a session."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM files WHERE session_id = ? ORDER BY last_changed_at_ms DESC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Tasks ---

    def upsert_task(self, task_id: str, session_id: str, **fields: Any) -> None:
        """Upsert task/plan item."""
        with self._lock:
            existing = self._conn.execute(
                "SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if existing:
                if fields:
                    set_clause = ", ".join(f"{k} = ?" for k in fields)
                    self._conn.execute(
                        f"UPDATE tasks SET {set_clause} WHERE task_id = ?",
                        (*fields.values(), task_id),
                    )
            else:
                all_fields = {"task_id": task_id, "session_id": session_id, **fields}
                all_fields.setdefault("subject", "unknown")
                all_fields.setdefault("status", "unknown")
                all_fields.setdefault("status_source", "inferred")
                cols = ", ".join(all_fields.keys())
                placeholders = ", ".join("?" for _ in all_fields)
                self._conn.execute(
                    f"INSERT INTO tasks ({cols}) VALUES ({placeholders})",
                    tuple(all_fields.values()),
                )
            self._conn.commit()

    def get_tasks(self, session_id: str) -> list[dict[str, Any]]:
        """Get tasks for a session."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE session_id = ? ORDER BY created_at_ms",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Alerts ---

    def insert_alert(
        self,
        session_id: str,
        severity: str,
        kind: str,
        message: str,
        created_at_ms: int,
    ) -> int:
        """Insert an alert. Returns alert id."""
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO alerts (session_id, severity, kind, message, created_at_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, severity, kind, message, created_at_ms),
            )
            self._conn.commit()
            return cur.lastrowid

    def resolve_alert(self, alert_id: int, resolved_at_ms: int) -> None:
        """Resolve an alert."""
        with self._lock:
            self._conn.execute(
                "UPDATE alerts SET resolved_at_ms = ? WHERE id = ?",
                (resolved_at_ms, alert_id),
            )
            self._conn.commit()

    def get_active_alerts(self, session_id: str) -> list[dict[str, Any]]:
        """Get unresolved alerts for a session."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM alerts
                   WHERE session_id = ? AND resolved_at_ms IS NULL
                   ORDER BY created_at_ms DESC""",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Instructions ---

    def upsert_instruction(
        self,
        session_id: str,
        file_path: str,
        scope: str | None = None,
        load_reason: str | None = None,
        loaded_at_ms: int | None = None,
    ) -> None:
        """Upsert an instruction source."""
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO instructions
                   (session_id, file_path, scope, load_reason, loaded_at_ms)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, file_path, scope, load_reason, loaded_at_ms),
            )
            self._conn.commit()

    def get_instructions(self, session_id: str) -> list[dict[str, Any]]:
        """Get loaded instructions for a session."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM instructions WHERE session_id = ? ORDER BY loaded_at_ms",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Timeline / raw events ---

    def get_timeline(
        self,
        session_id: str,
        limit: int = 200,
        offset: int = 0,
        event_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get raw events for timeline display."""
        with self._lock:
            if event_filter:
                rows = self._conn.execute(
                    """SELECT * FROM raw_events
                       WHERE session_id = ? AND event_name = ?
                       ORDER BY received_at_ms DESC LIMIT ? OFFSET ?""",
                    (session_id, event_filter, limit, offset),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM raw_events
                       WHERE session_id = ?
                       ORDER BY received_at_ms DESC LIMIT ? OFFSET ?""",
                    (session_id, limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]

    # --- Aggregate queries ---

    def get_session_summary(self, session_id: str) -> dict[str, Any]:
        """Get a summary of session state for the dashboard."""
        session = self.get_session(session_id) or {}
        agents = self.get_agents(session_id)
        active_agents = [a for a in agents if a["state"] not in (
            "finished", "finished_warn", "finished_error", "orphaned"
        )]
        alerts = self.get_active_alerts(session_id)
        files = self.get_files(session_id)
        instructions = self.get_instructions(session_id)

        return {
            "session": session,
            "agents": agents,
            "active_agent_count": len(active_agents),
            "active_alerts": alerts,
            "files_changed": len(files),
            "files": files,
            "instructions": instructions,
        }
