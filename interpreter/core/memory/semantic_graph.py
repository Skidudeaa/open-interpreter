"""
SemanticEditGraph - Persistent memory for code edits with semantic context.

This is the core component of the institutional memory system, providing:
- Storage of edit history with full context
- Querying edits by symbol, file, intent, or conversation
- Building relationships between edits over time
- Generating context for LLM prompts based on relevant past edits

Uses DuckDB for efficient storage and querying, falling back to SQLite
if DuckDB is not available.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
import logging

from .edit_record import (
    Edit,
    EditType,
    EditResult,
    ConversationContext,
    SymbolReference,
)

logger = logging.getLogger(__name__)


class SemanticEditGraph:
    """
    Persistent semantic memory for code edits.

    Tracks the relationship between code changes, conversations,
    and user intent to build institutional knowledge about a codebase.
    """

    def __init__(self, db_path: Optional[str] = None, use_duckdb: bool = True):
        """
        Initialize the semantic edit graph.

        Args:
            db_path: Path to database file. If None, uses in-memory database.
            use_duckdb: Try to use DuckDB (faster). Falls back to SQLite if unavailable.
        """
        self.db_path = db_path
        self._connection = None
        self._use_duckdb = use_duckdb and self._check_duckdb_available()

        if self._use_duckdb:
            self._init_duckdb()
        else:
            self._init_sqlite()

    def _check_duckdb_available(self) -> bool:
        """Check if DuckDB is installed."""
        try:
            import duckdb
            return True
        except ImportError:
            logger.info("DuckDB not available, falling back to SQLite")
            return False

    def _init_duckdb(self):
        """Initialize DuckDB connection and schema."""
        import duckdb

        if self.db_path:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._connection = duckdb.connect(self.db_path)
        else:
            self._connection = duckdb.connect(":memory:")

        self._create_schema_duckdb()

    def _init_sqlite(self):
        """Initialize SQLite connection and schema."""
        import sqlite3

        if self.db_path:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.db_path)
        else:
            self._connection = sqlite3.connect(":memory:")

        self._create_schema_sqlite()

    def _create_schema_duckdb(self):
        """Create DuckDB schema for edit storage."""
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS edits (
                id VARCHAR PRIMARY KEY,
                file_path VARCHAR NOT NULL,
                edit_type VARCHAR NOT NULL,
                user_intent TEXT,
                confidence DOUBLE,
                timestamp TIMESTAMP,
                git_commit_hash VARCHAR,
                parent_edit_id VARCHAR,
                execution_trace_id VARCHAR,
                data JSON NOT NULL
            )
        """)

        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY,
                edit_id VARCHAR NOT NULL,
                symbol_name VARCHAR NOT NULL,
                symbol_kind VARCHAR NOT NULL,
                file_path VARCHAR NOT NULL,
                is_primary BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (edit_id) REFERENCES edits(id)
            )
        """)

        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY,
                edit_id VARCHAR NOT NULL,
                conversation_id VARCHAR NOT NULL,
                turn_index INTEGER,
                user_message TEXT,
                intent_summary TEXT,
                FOREIGN KEY (edit_id) REFERENCES edits(id)
            )
        """)

        # Create indexes for common queries
        self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_edits_file_path ON edits(file_path)
        """)
        self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_edits_timestamp ON edits(timestamp)
        """)
        self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(symbol_name)
        """)
        self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_id ON conversations(conversation_id)
        """)

    def _create_schema_sqlite(self):
        """Create SQLite schema for edit storage."""
        cursor = self._connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS edits (
                id TEXT PRIMARY KEY,
                file_path TEXT NOT NULL,
                edit_type TEXT NOT NULL,
                user_intent TEXT,
                confidence REAL,
                timestamp TEXT,
                git_commit_hash TEXT,
                parent_edit_id TEXT,
                execution_trace_id TEXT,
                data TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edit_id TEXT NOT NULL,
                symbol_name TEXT NOT NULL,
                symbol_kind TEXT NOT NULL,
                file_path TEXT NOT NULL,
                is_primary INTEGER DEFAULT 0,
                FOREIGN KEY (edit_id) REFERENCES edits(id)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edit_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                turn_index INTEGER,
                user_message TEXT,
                intent_summary TEXT,
                FOREIGN KEY (edit_id) REFERENCES edits(id)
            )
        """)

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edits_file_path ON edits(file_path)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edits_timestamp ON edits(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(symbol_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_id ON conversations(conversation_id)")

        self._connection.commit()

    def record_edit(self, edit: Edit) -> str:
        """
        Record an edit to the semantic graph.

        Args:
            edit: The Edit object to record

        Returns:
            The edit ID
        """
        data_json = json.dumps(edit.to_dict())

        if self._use_duckdb:
            self._record_edit_duckdb(edit, data_json)
        else:
            self._record_edit_sqlite(edit, data_json)

        logger.debug(f"Recorded edit {edit.id} for {edit.file_path}")
        return edit.id

    def _record_edit_duckdb(self, edit: Edit, data_json: str):
        """Record edit using DuckDB."""
        self._connection.execute("""
            INSERT INTO edits (
                id, file_path, edit_type, user_intent, confidence,
                timestamp, git_commit_hash, parent_edit_id, execution_trace_id, data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            edit.id,
            edit.file_path,
            edit.edit_type.value,
            edit.user_intent,
            edit.confidence,
            edit.timestamp,
            edit.git_commit_hash,
            edit.parent_edit_id,
            edit.execution_trace_id,
            data_json,
        ])

        # Record symbols
        if edit.primary_symbol:
            self._connection.execute("""
                INSERT INTO symbols (edit_id, symbol_name, symbol_kind, file_path, is_primary)
                VALUES (?, ?, ?, ?, TRUE)
            """, [
                edit.id,
                edit.primary_symbol.name,
                edit.primary_symbol.kind,
                edit.primary_symbol.file_path,
            ])

        for symbol in edit.affected_symbols:
            self._connection.execute("""
                INSERT INTO symbols (edit_id, symbol_name, symbol_kind, file_path, is_primary)
                VALUES (?, ?, ?, ?, FALSE)
            """, [edit.id, symbol.name, symbol.kind, symbol.file_path])

        # Record conversation context
        if edit.conversation_context:
            ctx = edit.conversation_context
            self._connection.execute("""
                INSERT INTO conversations (
                    edit_id, conversation_id, turn_index, user_message, intent_summary
                ) VALUES (?, ?, ?, ?, ?)
            """, [
                edit.id,
                ctx.conversation_id,
                ctx.turn_index,
                ctx.user_message,
                ctx.intent_summary,
            ])

    def _record_edit_sqlite(self, edit: Edit, data_json: str):
        """Record edit using SQLite."""
        cursor = self._connection.cursor()

        cursor.execute("""
            INSERT INTO edits (
                id, file_path, edit_type, user_intent, confidence,
                timestamp, git_commit_hash, parent_edit_id, execution_trace_id, data
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            edit.id,
            edit.file_path,
            edit.edit_type.value,
            edit.user_intent,
            edit.confidence,
            edit.timestamp.isoformat(),
            edit.git_commit_hash,
            edit.parent_edit_id,
            edit.execution_trace_id,
            data_json,
        ))

        # Record symbols
        if edit.primary_symbol:
            cursor.execute("""
                INSERT INTO symbols (edit_id, symbol_name, symbol_kind, file_path, is_primary)
                VALUES (?, ?, ?, ?, 1)
            """, (
                edit.id,
                edit.primary_symbol.name,
                edit.primary_symbol.kind,
                edit.primary_symbol.file_path,
            ))

        for symbol in edit.affected_symbols:
            cursor.execute("""
                INSERT INTO symbols (edit_id, symbol_name, symbol_kind, file_path, is_primary)
                VALUES (?, ?, ?, ?, 0)
            """, (edit.id, symbol.name, symbol.kind, symbol.file_path))

        # Record conversation context
        if edit.conversation_context:
            ctx = edit.conversation_context
            cursor.execute("""
                INSERT INTO conversations (
                    edit_id, conversation_id, turn_index, user_message, intent_summary
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                edit.id,
                ctx.conversation_id,
                ctx.turn_index,
                ctx.user_message,
                ctx.intent_summary,
            ))

        self._connection.commit()

    def get_edit(self, edit_id: str) -> Optional[Edit]:
        """
        Retrieve an edit by ID.

        Args:
            edit_id: The edit ID

        Returns:
            The Edit object or None if not found
        """
        if self._use_duckdb:
            result = self._connection.execute(
                "SELECT data FROM edits WHERE id = ?", [edit_id]
            ).fetchone()
        else:
            cursor = self._connection.cursor()
            cursor.execute("SELECT data FROM edits WHERE id = ?", (edit_id,))
            result = cursor.fetchone()

        if result:
            return Edit.from_dict(json.loads(result[0]))
        return None

    def query_by_symbol(
        self,
        symbol_name: str,
        limit: int = 10,
        include_related: bool = True
    ) -> List[Edit]:
        """
        Find edits that affected a specific symbol.

        Args:
            symbol_name: Name of the symbol to search for
            limit: Maximum number of edits to return
            include_related: Include edits where symbol is related, not just affected

        Returns:
            List of Edit objects
        """
        if self._use_duckdb:
            query = """
                SELECT DISTINCT e.data
                FROM edits e
                JOIN symbols s ON e.id = s.edit_id
                WHERE s.symbol_name LIKE ?
                ORDER BY e.timestamp DESC
                LIMIT ?
            """
            results = self._connection.execute(query, [f"%{symbol_name}%", limit]).fetchall()
        else:
            cursor = self._connection.cursor()
            cursor.execute("""
                SELECT DISTINCT e.data
                FROM edits e
                JOIN symbols s ON e.id = s.edit_id
                WHERE s.symbol_name LIKE ?
                ORDER BY e.timestamp DESC
                LIMIT ?
            """, (f"%{symbol_name}%", limit))
            results = cursor.fetchall()

        return [Edit.from_dict(json.loads(row[0])) for row in results]

    def query_by_file(
        self,
        file_path: str,
        limit: int = 20
    ) -> List[Edit]:
        """
        Find all edits for a specific file.

        Args:
            file_path: Path to the file
            limit: Maximum number of edits to return

        Returns:
            List of Edit objects, most recent first
        """
        if self._use_duckdb:
            results = self._connection.execute("""
                SELECT data FROM edits
                WHERE file_path = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, [file_path, limit]).fetchall()
        else:
            cursor = self._connection.cursor()
            cursor.execute("""
                SELECT data FROM edits
                WHERE file_path = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (file_path, limit))
            results = cursor.fetchall()

        return [Edit.from_dict(json.loads(row[0])) for row in results]

    def query_by_intent(
        self,
        intent_keywords: str,
        limit: int = 10
    ) -> List[Edit]:
        """
        Find edits with matching intent.

        Args:
            intent_keywords: Keywords to search for in user intent
            limit: Maximum number of edits to return

        Returns:
            List of Edit objects
        """
        if self._use_duckdb:
            results = self._connection.execute("""
                SELECT data FROM edits
                WHERE user_intent LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, [f"%{intent_keywords}%", limit]).fetchall()
        else:
            cursor = self._connection.cursor()
            cursor.execute("""
                SELECT data FROM edits
                WHERE user_intent LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (f"%{intent_keywords}%", limit))
            results = cursor.fetchall()

        return [Edit.from_dict(json.loads(row[0])) for row in results]

    def query_by_conversation(
        self,
        conversation_id: str
    ) -> List[Edit]:
        """
        Find all edits from a specific conversation.

        Args:
            conversation_id: The conversation ID

        Returns:
            List of Edit objects in chronological order
        """
        if self._use_duckdb:
            results = self._connection.execute("""
                SELECT e.data
                FROM edits e
                JOIN conversations c ON e.id = c.edit_id
                WHERE c.conversation_id = ?
                ORDER BY c.turn_index ASC
            """, [conversation_id]).fetchall()
        else:
            cursor = self._connection.cursor()
            cursor.execute("""
                SELECT e.data
                FROM edits e
                JOIN conversations c ON e.id = c.edit_id
                WHERE c.conversation_id = ?
                ORDER BY c.turn_index ASC
            """, (conversation_id,))
            results = cursor.fetchall()

        return [Edit.from_dict(json.loads(row[0])) for row in results]

    def get_institutional_knowledge(
        self,
        file_path: str,
        max_edits: int = 10
    ) -> str:
        """
        Generate a summary of historical context for a file.

        This is used to provide the LLM with institutional knowledge
        about why and how a file was modified in the past.

        Args:
            file_path: Path to the file
            max_edits: Maximum number of edits to include

        Returns:
            A formatted string suitable for LLM context
        """
        edits = self.query_by_file(file_path, limit=max_edits)

        if not edits:
            return f"No edit history found for {file_path}"

        parts = [f"## Edit History for {file_path}", ""]

        for edit in edits:
            parts.append(edit.to_context_string())
            parts.append("")

        # Add summary statistics
        edit_types = {}
        for edit in edits:
            edit_types[edit.edit_type.value] = edit_types.get(edit.edit_type.value, 0) + 1

        parts.append("### Summary")
        parts.append(f"Total edits: {len(edits)}")
        for edit_type, count in sorted(edit_types.items(), key=lambda x: -x[1]):
            parts.append(f"  {edit_type}: {count}")

        return "\n".join(parts)

    def get_related_edits(
        self,
        edit: Edit,
        limit: int = 5
    ) -> List[Edit]:
        """
        Find edits related to a given edit based on shared symbols.

        Args:
            edit: The edit to find relatives for
            limit: Maximum number of related edits

        Returns:
            List of related Edit objects
        """
        # Get all symbol names from this edit
        symbol_names = edit.get_affected_symbol_names()

        if not symbol_names:
            return []

        related_edits = []
        seen_ids = {edit.id}

        for symbol_name in symbol_names:
            edits = self.query_by_symbol(symbol_name, limit=limit)
            for e in edits:
                if e.id not in seen_ids:
                    related_edits.append(e)
                    seen_ids.add(e.id)
                    if len(related_edits) >= limit:
                        break
            if len(related_edits) >= limit:
                break

        return related_edits[:limit]

    def get_edit_chain(self, edit_id: str) -> List[Edit]:
        """
        Get the chain of edits (parent edits and refinements).

        Args:
            edit_id: Starting edit ID

        Returns:
            List of Edit objects from oldest ancestor to newest
        """
        chain = []
        current_id = edit_id

        # Walk up the parent chain
        parent_chain = []
        while current_id:
            edit = self.get_edit(current_id)
            if edit:
                parent_chain.append(edit)
                current_id = edit.parent_edit_id
            else:
                break

        # Reverse to get chronological order
        chain = list(reversed(parent_chain))

        return chain

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about the edit graph.

        Returns:
            Dictionary with edit statistics
        """
        if self._use_duckdb:
            total = self._connection.execute("SELECT COUNT(*) FROM edits").fetchone()[0]
            by_type = self._connection.execute("""
                SELECT edit_type, COUNT(*) as count
                FROM edits
                GROUP BY edit_type
                ORDER BY count DESC
            """).fetchall()
            unique_files = self._connection.execute(
                "SELECT COUNT(DISTINCT file_path) FROM edits"
            ).fetchone()[0]
            unique_symbols = self._connection.execute(
                "SELECT COUNT(DISTINCT symbol_name) FROM symbols"
            ).fetchone()[0]
        else:
            cursor = self._connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM edits")
            total = cursor.fetchone()[0]
            cursor.execute("""
                SELECT edit_type, COUNT(*) as count
                FROM edits
                GROUP BY edit_type
                ORDER BY count DESC
            """)
            by_type = cursor.fetchall()
            cursor.execute("SELECT COUNT(DISTINCT file_path) FROM edits")
            unique_files = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(DISTINCT symbol_name) FROM symbols")
            unique_symbols = cursor.fetchone()[0]

        return {
            "total_edits": total,
            "by_type": {row[0]: row[1] for row in by_type},
            "unique_files": unique_files,
            "unique_symbols": unique_symbols,
        }

    def close(self):
        """Close the database connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
