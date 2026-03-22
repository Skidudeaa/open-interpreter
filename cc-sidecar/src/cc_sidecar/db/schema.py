"""
SQLite schema for cc-sidecar.

Tables:
    raw_events   — immutable event log (source of truth)
    sessions     — materialized session state
    agents       — agent state (main thread + subagents)
    tool_calls   — tool lifecycle tracking
    tasks        — plan/task items
    files        — changed files with ownership
    alerts       — blocked/stuck/orphaned/compaction alerts

All derived tables are rebuildable from raw_events via reducer replay.
"""

from __future__ import annotations

SCHEMA_VERSION = 2

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Immutable raw event log
CREATE TABLE IF NOT EXISTS raw_events (
    id INTEGER PRIMARY KEY,
    received_at_ms INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    session_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,          -- hook | statusline | eventbus | git | transcript
    event_name TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_raw_events_session
    ON raw_events(session_id, received_at_ms);
CREATE INDEX IF NOT EXISTS idx_raw_events_name
    ON raw_events(event_name, received_at_ms);

-- Materialized session state
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    cwd TEXT,
    project_dir TEXT,
    model TEXT,
    claude_version TEXT,
    source TEXT,                        -- startup | resume | clear | compact
    started_at_ms INTEGER,
    last_seen_at_ms INTEGER,
    ended_at_ms INTEGER,
    end_reason TEXT,
    context_used_pct REAL,
    context_remaining_pct REAL,
    total_cost_usd REAL,
    total_duration_ms INTEGER,
    total_lines_added INTEGER,
    total_lines_removed INTEGER,
    worktree_path TEXT,
    worktree_branch TEXT,
    compaction_count INTEGER DEFAULT 0,
    last_compaction_at_ms INTEGER,
    current_user_ask TEXT,
    last_assistant_summary TEXT
);

-- Agent state (main thread as synthetic agent + subagents)
CREATE TABLE IF NOT EXISTS agents (
    agent_pk TEXT PRIMARY KEY,          -- main:<session_id> or sub:<agent_id>
    session_id TEXT NOT NULL,
    agent_id TEXT,
    agent_type TEXT NOT NULL,
    state TEXT NOT NULL,                -- idle | running_tool | awaiting_perm
                                       -- | blocked | retrying | compacting
                                       -- | finished[_warn|_error] | orphaned
    state_source TEXT NOT NULL,         -- observed | inferred
    started_at_ms INTEGER,
    last_event_at_ms INTEGER,
    stopped_at_ms INTEGER,
    last_tool_name TEXT,
    last_resource TEXT,
    last_summary TEXT,
    visibility_mode TEXT NOT NULL DEFAULT 'lifecycle_only',  -- full | lifecycle_only
    current_activity_type TEXT,         -- think | search | read | plan
                                       -- | edit | execute | validate | wait
    current_activity_message TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_agents_session
    ON agents(session_id, state);
CREATE INDEX IF NOT EXISTS idx_agents_agent_id
    ON agents(agent_id);

-- Tool call lifecycle
CREATE TABLE IF NOT EXISTS tool_calls (
    tool_use_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_pk TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL,               -- started | success | failure | denied
    started_at_ms INTEGER NOT NULL,
    ended_at_ms INTEGER,
    input_preview TEXT,                 -- human-readable resource summary
    output_preview TEXT,
    error TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (agent_pk) REFERENCES agents(agent_pk)
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_agent
    ON tool_calls(agent_pk, started_at_ms);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session
    ON tool_calls(session_id, started_at_ms);

-- Task/plan items
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    description TEXT,
    owner_agent_pk TEXT,
    status TEXT NOT NULL,               -- planned | running | blocked | completed | unknown
    status_source TEXT NOT NULL,        -- observed | custom_plan | inferred
    created_at_ms INTEGER,
    completed_at_ms INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_session
    ON tasks(session_id, created_at_ms);

-- Changed files with ownership attribution
CREATE TABLE IF NOT EXISTS files (
    session_id TEXT NOT NULL,
    path TEXT NOT NULL,
    last_writer_agent_pk TEXT,
    ownership_source TEXT NOT NULL,     -- observed | inferred | unknown
    added_lines INTEGER,
    removed_lines INTEGER,
    git_status TEXT,
    last_changed_at_ms INTEGER,
    PRIMARY KEY (session_id, path)
);

-- Activity history (append-only timeline of agent activities)
-- WHY: current_activity_type on agents is overwritten on each update,
-- losing the think→search→read→edit→validate chain. This table preserves
-- the full sequence for post-session analysis and timeline display.
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    agent_pk TEXT NOT NULL,
    activity_type TEXT NOT NULL,         -- think | search | read | plan
                                       -- | edit | execute | validate | wait
    message TEXT,
    started_at_ms INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
    FOREIGN KEY (agent_pk) REFERENCES agents(agent_pk)
);

CREATE INDEX IF NOT EXISTS idx_activities_agent
    ON activities(agent_pk, started_at_ms);
CREATE INDEX IF NOT EXISTS idx_activities_session
    ON activities(session_id, started_at_ms);

-- Alerts
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    severity TEXT NOT NULL,             -- info | warn | error
    kind TEXT NOT NULL,                 -- permission_denied | stuck | orphaned
                                       -- | compaction | config_change | test_failure
    message TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    resolved_at_ms INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_alerts_session
    ON alerts(session_id, resolved_at_ms);

-- Instruction/rule sources loaded into context
CREATE TABLE IF NOT EXISTS instructions (
    session_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    scope TEXT,                         -- user | project | local | managed
    load_reason TEXT,                   -- session_start | nested_traversal
                                       -- | path_glob | include | compact
    loaded_at_ms INTEGER,
    PRIMARY KEY (session_id, file_path)
);
"""


def get_schema_sql() -> str:
    """Return the full schema SQL."""
    return SCHEMA_SQL


def get_schema_version() -> int:
    """Return the current schema version."""
    return SCHEMA_VERSION
