# Personal AI Memory System

## What This Is

An extension to Open Interpreter that transforms it from a reactive assistant into a personal AI system that learns from your behavior. The system observes patterns, remembers outcomes, tracks tasks, and uses this accumulated knowledge to shape how it responds — making it increasingly attuned to how you work over time.

## Core Value

Past behavior compounds into future capability — the system learns YOU.

## Requirements

### Validated

- ✓ Semantic edit tracking via SemanticEditGraph — existing
- ✓ Event-driven architecture with EventBus — existing
- ✓ Multi-agent orchestration (Scout, Surgeon, Architect, Validator) — existing
- ✓ Edit validation with syntax checking and rollback — existing
- ✓ Runtime execution tracing — existing
- ✓ LiteLLM abstraction for 100+ models — existing
- ✓ Generator-based streaming for responses — existing
- ✓ Observability pipeline: EventBus → ObservabilityBridge → cc-sidecar daemon — shipped 2026-04
- ✓ Textual TUI backend with agent widgets and reactive state — shipped 2026-04
- ✓ Sidecar security: owner-only file permissions, payload sanitization — shipped 2026-04
- ✓ Thread-safe agent tracking with _active_agent attribution — shipped 2026-04

### Active — Memory Layer (next up)

- [ ] Memory layer extending SemanticEditGraph for four memory types
- [ ] Preference memory: explicit declarations ("I prefer X over Y")
- [ ] Outcome memory: success/failure tracking with causal attribution
- [ ] Task state memory: hierarchical (project → milestone → task → subtask)
- [ ] Context pattern memory: behavioral inference ("debug mode after midnight")
- [ ] Signal ingestion: explicit statements captured and stored
- [ ] Signal ingestion: git activity (commits, rollbacks, diffs)
- [ ] Signal ingestion: session context (commands, files, errors)
- [ ] Signal ingestion: time/environment (when, where, app focus)
- [ ] Pre-prompting mechanism: memory shapes context before LLM sees request
- [ ] Memory retrieval: relevance matching for current context
- [ ] Preference learning: explicit > implicit, both decay when contradicted
- [ ] Causal inference: infer when obvious, ask when ambiguous

### Recommended Starting Point

**Preference memory + pre-prompting** proves the architecture end-to-end with the smallest surface area:
1. Store explicit preferences in SemanticEditGraph (extend existing DuckDB/SQLite)
2. Retrieve relevant preferences before each LLM call in `respond.py`
3. Inject as system message context (pre-prompting)
4. This validates: storage, retrieval, relevance matching, and influence mechanism

**Integration points already wired:**
- `respond.py` execution loop — insert retrieval before system message assembly
- `EventBus` SYSTEM_START events — carry user prompt text for relevance matching
- `ObservabilityBridge` — already captures session events for durable storage
- `SemanticEditGraph` — extend schema for preference records alongside edit records

### Out of Scope (v2)

- Suggestion injection ("Based on past...") — requires relevance detection
- Constraint application (warns/refuses based on past failures) — requires confidence thresholds
- Auto-decisions (strong priors bypass LLM) — requires high-confidence preference learning
- Aggressive proactive surfacing — only relevant-only surfacing in scope
- Continuous observation (passive filesystem/OS watching) — v1 is invocation-based
- Intent routing layer (classifier before LLM) — not needed for pre-prompting proof

## Context

**Brownfield project** extending an Open Interpreter fork with:
- Existing `SemanticEditGraph` in `interpreter/core/memory/` using DuckDB/SQLite
- Event-driven UI with `EventBus` for decoupled component communication
- Multi-agent orchestration with context passing between agents
- Response layer with hooks for validation, tracing, memory recording
- Observability pipeline capturing all session events to cc-sidecar SQLite

**Existing memory infrastructure:**
- `interpreter/core/memory/semantic_graph.py` — Edit tracking with symbol extraction
- `interpreter/core/memory/edit_record.py` — Edit representation with context
- `interpreter/core/memory/conversation_linker.py` — Links edits to conversations
- Storage: DuckDB with SQLite fallback

**Integration points:**
- `respond.py` manages execution loop — pre-prompting hooks here
- `UIState` and `EventBus` for surfacing memory-influenced behavior
- `interpreter.messages` list holds conversation history
- `cc-sidecar/db/store.py` holds durable session/agent/activity history

## Constraints

- **Tech stack**: Extend existing DuckDB/SQLite infrastructure — no new storage systems
- **Architecture**: Memory layer must integrate with existing generator-based streaming
- **Influence mechanism**: v1 proves pre-prompting only — other mechanisms deferred
- **Scope**: "Prove architecture" — all memory types with basic implementations
- **Personal system**: Single-user, local-first, no multi-tenancy considerations

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Pre-prompting as first influence mechanism | Simplest integration, proves retrieval works | — Pending |
| Extend SemanticEditGraph rather than new storage | Leverage existing infrastructure, reduce complexity | — Pending |
| All four memory types in v1 (basic impl) | Prove architecture handles multiple types | — Pending |
| All four signal sources in v1 | Prove architecture handles multiple inputs | — Pending |
| Explicit > implicit preferences with decay | More predictable, contradictions override | — Pending |
| Ask when causal inference uncertain | Avoid confident wrong attributions | — Pending |
| Hierarchical task state | Matches natural project structure | — Pending |
| Relevant-only proactive surfacing | Useful without being annoying | — Pending |

---
*Last updated: 2026-04-04 after infrastructure completion (observability, agents, TUI, security hardening)*
