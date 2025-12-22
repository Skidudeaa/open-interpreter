# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Open Interpreter fork that adds risk-based approval, semantic memory, multi-agent orchestration, edit validation, and an event-driven terminal UI. LLMs run code locally via a chat interface.

## Commands

```bash
# Install dependencies
poetry install
poetry install -E local    # For --local mode
poetry install -E os       # For --os mode
poetry install -E server   # For server features

# Run application
poetry run interpreter

# Run tests
poetry run pytest -s -x

# Run single test
poetry run pytest -s -x tests/test_file.py::test_name

# Code formatting (automatic via pre-commit, or manual)
black .
isort .
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
```

### Key Modules

**`interpreter/core/`**
- `core.py` - Main `OpenInterpreter` class orchestrating LLM ↔ Computer loop
- `respond.py` - Execution loop with system message caching and network status tracking
- `memory/` - Semantic edit tracking (DuckDB/SQLite) with `SemanticEditGraph`
- `tracing/` - Runtime execution tracing via `sys.settrace`
- `agents/` - Multi-agent orchestration (`ScoutAgent`, `SurgeonAgent`, `AgentOrchestrator`)
- `validation/` - Edit validation with syntax checking, test discovery, git-based rollback
- `computer/` - System interface (terminal, display, keyboard, mouse, files, browser)
- `llm/` - LiteLLM abstraction supporting 100+ models

**`interpreter/terminal_interface/`**
- Event-driven UI using `EventBus` for decoupled component communication
- `UIState` as single source of truth for mode/agents/panels/tokens
- Dual backends: `PromptToolkitBackend` (interactive) / `RichStreamBackend` (fallback)
- Adaptive modes: ZEN → STANDARD → POWER → DEBUG (auto-escalates based on activity score)

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
# Or: interpreter.activate_all_features()
```

### Environment Variables

```bash
OI_ACTIVATE_ALL=true              # Enable all advanced features
OPEN_INTERPRETER_APPROVAL=dangerous  # Risk-based approval (off/dangerous/all)
OI_UI_DEBUG=true                  # Debug logging to ~/.open-interpreter/logs/
OI_NO_TUI=true                    # Disable interactive mode
```

## Key Patterns

1. **Event-Driven UI** - Components subscribe to `EventBus` events (`AGENT_SPAWN`, `CODE_START`, etc.)
2. **Lazy Loading** - Memory, validation, tracing, agents loaded on first use (thread-safe double-checked locking)
3. **Generator-Based Streaming** - `respond()` yields chunks for real-time display
4. **Git-Based Rollback** - `TransactionalEdit` context manager for atomic file changes

## Code Style

- Black formatter (88 char line limit, target Python 3.11)
- isort (black profile)
- Pre-commit hooks auto-format on commit

## License

MIT for versions <0.2.0, AGPL for subsequent contributions.
