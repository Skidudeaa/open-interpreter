# cc-sidecar

Passive, event-sourced observability sidecar for Claude Code.

## What it does

cc-sidecar is a daemon that ingests lifecycle events from Claude Code (and forks like this one) over a Unix socket, materializes them into a SQLite event store, and exposes the state over a WebSocket feed for real-time dashboards.

The sidecar is **passive**: it only observes. It never blocks or modifies the host agent.

## Data flow

```
Host agent (Claude Code, fork, Sinter, etc.)
    → EventBus / hooks / stdin
    → ingest transport (fire-and-forget, spool-to-disk fallback)
    → daemon (Unix socket listener)
    → reducer (event-sourced state machine)
    → SQLite store (WAL mode, owner-only permissions)
    → WebSocket feed → TUI / dashboards
```

## Install

```bash
# From the fork root (installs as a path dependency)
poetry install

# Standalone
cd cc-sidecar && poetry install
```

## Usage

```bash
cc-sidecar daemon          # Start the daemon
cc-sidecar status          # Check daemon status
```

## Development

```bash
poetry run pytest tests/ -x
```

## Key design choices

- **Event-sourced**: raw events are stored first, then reduced into materialized state. The reducer is idempotent — replaying events yields the same state.
- **Owner-only permissions**: DB (0o600), spool files (0o600), directories (0o700).
- **Schema-versioned**: v3 adds `file_includes` for @path context tracking.

## License

MIT
