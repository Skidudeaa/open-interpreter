# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Open Interpreter fork that adds risk-based approval, semantic memory, multi-agent orchestration, edit validation, an event-driven terminal UI, and a cc-sidecar observability daemon. LLMs run code locally via a chat interface.

## Usage

```bash
# Basic
poetry run interpreter

# All features enabled (agents, memory, validation, tracing, observability)
OI_ACTIVATE_ALL=true poetry run interpreter

# Auto-approve code execution
poetry run interpreter -y

# Specific model
poetry run interpreter --model gpt-4o

# Local mode (ollama)
poetry run interpreter --local

# OS mode (computer control)
poetry run interpreter --os

# Non-interactive (pipe input)
echo "list files" | poetry run interpreter

# Debug logging
OI_UI_DEBUG=true poetry run interpreter

# Full-screen Textual TUI (experimental)
poetry run interpreter --tui

# With sidecar observability
poetry run interpreter --observability
```

## Dev Commands

```bash
poetry install                    # Install deps
poetry run pytest -s -x           # Run tests
poetry run pytest tests/test_x.py::test_name  # Single test

# cc-sidecar (separate package, src-layout)
poetry run pytest cc-sidecar/tests/ -x  # Sidecar tests
cc-sidecar daemon                       # Start sidecar daemon
cc-sidecar status                       # Check daemon status
```

## Architecture

### Core Execution Flow

```
User Request → OpenInterpreter.chat() → respond() loop
    ↓
LLM (via LiteLLM) generates code
    ↓
Computer.run() executes code
    ↓
Output fed back to LLM until complete
    ↓
EventBus → ObservabilityBridge → cc-sidecar daemon (if enabled)
```

### Key Modules

**`interpreter/core/`**
- `core.py` - Main `OpenInterpreter` class orchestrating LLM ↔ Computer loop
- `respond.py` - Execution loop with system message caching and network status tracking
- `observability.py` - `ObservabilityBridge` translates EventBus events → cc-sidecar format
- `memory/` - Semantic edit tracking (DuckDB/SQLite) with `SemanticEditGraph`
- `tracing/` - Runtime execution tracing via `sys.settrace`
- `agents/` - Multi-agent orchestration (`ScoutAgent`, `SurgeonAgent`, `AgentOrchestrator`)
- `validation/` - Edit validation with syntax checking, test discovery, git-based rollback
- `computer/` - System interface (terminal, display, keyboard, mouse, files, browser)
- `llm/` - LiteLLM abstraction supporting 100+ models

**`interpreter/terminal_interface/`**
- Event-driven UI using `EventBus` for decoupled component communication
- `UIState` as single source of truth for mode/agents/panels/tokens
- Three backends: `PromptToolkitBackend` (default), `TextualBackend` (`--tui`), `RichStreamBackend` (`--no-tui`)
- Adaptive modes: ZEN → STANDARD → POWER → DEBUG (auto-escalates based on activity score)
- `textual_app.py` - Full-screen Textual `App` with reactive state, CSS theming, agent widgets

**`interpreter/terminal_interface/components/`** (UI Components)
- `ui_state.py` - Centralized state: `UIState`, `UIMode`, `AgentState`, `AgentStatus`
- `ui_events.py` - `EventBus` pub/sub with `EventType` enum (`AGENT_SPAWN`, `CODE_START`, etc.)
- `ui_mode_manager.py` - Auto-escalation scoring (agent spawn +10, error +5, long exec +3)
- `input_handler.py` - prompt_toolkit key bindings (Alt+P mode, Alt+H panel, Ctrl+R history)
- `completers.py` - `MagicCommandCompleter`, `ConversationCompleter`, `AtFileCompleter`
- `command_palette.py` - Fuzzy `/` command search with recent prioritization
- `pt_app.py` - Full-screen prompt_toolkit `Application` with layout
- `agent_strip.py` - Bottom bar: `[Scout: ✓ 2.3s] [Surgeon: ⏳ running]`
- `agent_tree.py` - Hierarchical parent→child agent view with output preview
- `context_panel.py` - Variables/functions/metrics sidebar (POWER/DEBUG mode)
- `context_meter.py` - Token usage bar with threshold warnings
- `code_block.py` - Syntax-highlighted code with fold/unfold, traceback highlighting
- `toast.py` - Transient notification system

**`interpreter/terminal_interface/utils/`**
- `clipboard.py` - Shared clipboard logic (copy-to-clipboard, content extraction) used by both backends

**`cc-sidecar/`** (Observability Daemon — separate src-layout package)
- `daemon/server.py` - Unix socket listener + WebSocket server for real-time event streaming
- `reducer/state_machine.py` - Event-sourced reducer: raw events → materialized session/agent/file state
- `db/store.py` - Thread-safe SQLite store (WAL mode, owner-only permissions)
- `ingest/transport.py` - Fire-and-forget transport with spool-to-disk fallback
- Data flow: `EventBus → ObservabilityBridge → transport → daemon → reducer → SQLite`

**`interpreter/sdk/`**
- `AgentBuilder` - Factory for custom agents from templates (scout, surgeon, architect, reviewer, tester)
- Plugin system with hooks: `on_before_execute`, `on_after_execute`, `on_error`, etc.
- `MCPBridge` - Model Context Protocol integration

### Feature Activation

Features are lazy-loaded and disabled by default:

```bash
OI_ACTIVATE_ALL=true poetry run interpreter  # Enable all features
```

```python
interpreter.enable_semantic_memory = True
interpreter.enable_validation = True
interpreter.enable_tracing = True
interpreter.enable_agents = True
interpreter.enable_observability = True
# Or: interpreter.activate_all_features()
```

### Environment Variables

```bash
# Feature toggles
OI_ACTIVATE_ALL=true              # Enable all advanced features
OPEN_INTERPRETER_APPROVAL=dangerous  # Risk-based approval (off/dangerous/all)
OI_UI_DEBUG=true                  # Debug logging to ~/.open-interpreter/logs/
OI_NO_TUI=true                    # Disable interactive mode (Rich streaming only)
OI_MODEL=gemini/gemini-3.1-pro    # Default model (also settable via --model)

# Sidecar observability
CLAUDE_SESSION_ID=abc123          # Session ID for sidecar (auto-generated if absent)
CC_SIDECAR_SOCKET=/path/to/sock   # Override daemon socket path
CC_SIDECAR_SPOOL_DIR=/path/to/dir # Override spool directory
```

## Key Patterns

1. **Event-Driven UI** - Components subscribe to `EventBus` events (`AGENT_SPAWN`, `CODE_START`, etc.)
2. **Lazy Loading** - Memory, validation, tracing, agents loaded on first use (thread-safe double-checked locking)
3. **Generator-Based Streaming** - `respond()` yields chunks for real-time display
4. **Git-Based Rollback** - `TransactionalEdit` context manager for atomic file changes
5. **Exit via Exception** - Ctrl+C/D use `event.app.exit(exception=EOFError())` so prompt_toolkit raises instead of returning empty string
6. **Payload Sanitization** - ObservabilityBridge uses per-event-type field allowlists to prevent leaking secrets into sidecar DB
7. **Owner-Only Permissions** - Sidecar DB (0o600), spool files (0o600), directories (0o700)

## Terminal UI Architecture

### Key Bindings (unified across both backends)

| Key | Action |
|-----|--------|
| `Ctrl+L` | Clear screen |
| `Ctrl+R` | Search history |
| `Ctrl+D` | Exit (empty buffer) |
| `Ctrl+Shift+C` | Copy last response |
| `Alt+P` | Cycle UI mode (Option+P on Mac) |
| `Alt+H` | Toggle context panel (Option+H on Mac) |
| `Alt+A` | Focus agent strip (Option+A on Mac) |
| `Alt+S` | Toggle selection mode (Option+S on Mac) |
| `Alt+C` | Copy last response (Option+C on Mac) |
| `Alt+?` | Show help overlay (Option+? on Mac) |
| `Alt+T` | Cycle theme (Textual only) |
| `Esc` | Cancel operation |

### Event Types (ui_events.py)

```python
# Agent lifecycle
AGENT_SPAWN, AGENT_COMPLETE, AGENT_ERROR, AGENT_OUTPUT

# Code execution
CODE_START, CODE_END, CONSOLE_OUTPUT, CONSOLE_ERROR

# System
SYSTEM_START, SYSTEM_END, SYSTEM_TOKEN_UPDATE, SYSTEM_ERROR, UI_MODE_CHANGE, UI_CANCEL
```

### UI Modes (auto-escalation)

| Mode | Score | Visible Elements |
|------|-------|------------------|
| ZEN | 0 | Conversation only |
| STANDARD | 5+ | + Status bar |
| POWER | 15+ | + Agent strip, context panel |
| DEBUG | 30+ | + Token counts, timing, raw chunks |

Scoring: Agent spawn +10, error +5, code exec +3, long run +3. Decays 1 pt/30s.

## Code Style

- Black formatter (88 char line limit, target Python 3.11)
- isort (black profile)
- Pre-commit hooks auto-format on commit

## Common Errors to Avoid

- Never bind the server or tests to port 8000 — it's reserved for another project on this machine. Use port 8123 (or a random free port) instead.
- Don't use F-keys for TUI bindings — they're system keys on Mac (brightness, Mission Control) and unavailable on iPad keyboards. Use Ctrl+key or Alt+key combos.
- Check for argparse flag conflicts when adding new CLI arguments (e.g., `-t` was already taken by `--temperature` when `--tui` was added).
- Pre-commit has ruff-format and black both enabled — they fight on 2 files (`state_machine.py`, `async_core.py`). Use `SKIP=ruff-format git commit` to work around until one formatter is removed.
- Textual TUI requires full terminal control — won't work in some SSH clients or iPad terminals. Falls back gracefully if unavailable.

## Next Up: Memory Layer

The memory system is the project's north star (see `.planning/PROJECT.md`). Infrastructure is complete:
- Agents (Scout, Surgeon, Orchestrator) are wired into `respond.py`
- EventBus captures session lifecycle events
- ObservabilityBridge feeds cc-sidecar for durable event storage
- SemanticEditGraph in `interpreter/core/memory/` provides DuckDB/SQLite storage

**First items to implement:**
1. Preference memory — explicit declarations stored and retrieved
2. Pre-prompting — inject relevant memories into system message before LLM sees the request
3. Signal ingestion — capture explicit statements, git activity, session context

Integration point: `respond.py` execution loop, before system message is sent to LLM.

## License

MIT for versions <0.2.0, AGPL for subsequent contributions.
