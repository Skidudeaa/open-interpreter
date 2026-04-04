# Changelog

## [0.5.0] - 2026-04-04 (Fork Release)

This is the first consolidated release of the Skidudeaa fork. Everything below was built on top of upstream Open Interpreter v0.4.3 (the final CLI release before the upstream project pivoted to a desktop app).

### Multi-Agent System
- **AgentOrchestrator** — Classifies requests into workflows (EXPLORE, EDIT, FULL, VALIDATE, NONE) and dispatches specialized agents
- **ScoutAgent** — Codebase search with ripgrep, AST symbol extraction, LLM synthesis. 15k file scanning limit, index caching
- **SurgeonAgent** — Precise code edits with atomic transactions, path traversal prevention, syntax validation, content hash verification
- **Per-agent model routing** — Scout on a fast model, Surgeon on a strong model
- **SDK** — `AgentBuilder` factory with templates (scout, surgeon, architect, reviewer, tester), plugin hooks, swarm coordination

### Observability (cc-sidecar)
- **Daemon** — Unix socket listener + WebSocket server for real-time event streaming
- **Reducer** — Event-sourced state machine: raw events → materialized session/agent/file state in SQLite
- **ObservabilityBridge** — Translates fork's EventBus events to sidecar format with per-event-type field allowlists
- **Transport** — Fire-and-forget with spool-to-disk fallback (50MB cap)
- **Security** — Owner-only file permissions (0o700 dirs, 0o600 files), payload sanitization, no secrets in storage

### Terminal UI
- **Three backends** — prompt_toolkit (default), Textual full-screen (`--tui`), Rich streaming (`--no-tui`)
- **Adaptive modes** — ZEN → STANDARD → POWER → DEBUG, auto-escalates based on activity score
- **Agent strip** — Real-time status bar: `[Scout: ✓ 2.3s] [Surgeon: ⏳ running]`
- **Agent tree** — Hierarchical parent→child view with output preview
- **Context panel** — Variables, functions, metrics sidebar (POWER/DEBUG mode)
- **Context meter** — Token usage bar with threshold warnings (green→yellow→red)
- **Unified key bindings** — Alt+P/H/A/S/C/? across both interactive backends
- **Shared clipboard** — `clipboard.py` used by both backends with pyperclip error handling

### Edit Safety
- **EditValidator** — Syntax checking (Python, JS, TS, JSON, shell), test discovery, git-based rollback
- **TransactionalEdit** — Context manager for atomic file changes with git stash backup
- **Auto-test** — Runs related tests after file modifications, feeds failures to LLM

### Memory & Tracing
- **SemanticEditGraph** — DuckDB/SQLite tracking of WHY code changed (symbol extraction, conversation linking)
- **ExecutionTracer** — `sys.settrace`-based call graph capture with LLM-readable context generation
- **@file references** — Type `@filename` in CLI, tab-completes, prepends file content as context

### Risk-Based Approval
- `OPEN_INTERPRETER_APPROVAL=dangerous` — Auto-approves safe ops, prompts only on destructive commands (rm, sudo, network)

### Performance
- Lazy-loading with thread-safe double-checked locking for all feature modules
- String accumulation O(n²) → O(n), system message caching, reverse iteration for message scanning
- Non-blocking startup, index caching for Scout searches

### Tests
- 455+ tests passing across core, agents, UI, sidecar
- Coverage for: agent attribution, _ensure_session fallback, ObservabilityBridge routing, payload sanitization, file permissions, clipboard handling, Textual binding validation

---

*Built on upstream Open Interpreter v0.4.3 (Oct 2024). Upstream has not released a CLI update since.*
