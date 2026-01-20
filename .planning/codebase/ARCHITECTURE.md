# Architecture

**Analysis Date:** 2026-01-19

## Pattern Overview

**Overall:** Event-Driven Multi-Agent Orchestration with Generator-Based Streaming

**Key Characteristics:**
- Generator-based streaming from LLM through execution to UI
- Lazy-loaded subsystems (memory, validation, tracing, agents) with thread-safe double-checked locking
- Event bus decouples interpreter core from UI components
- Multi-agent orchestration for complex coding tasks
- LiteLLM abstraction for 100+ model support

## Layers

**Interpreter Core (`interpreter/core/core.py`):**
- Purpose: Central orchestration hub - the "grand central station"
- Location: `interpreter/core/core.py`
- Contains: `OpenInterpreter` class, feature flag management, lazy property loading
- Depends on: LLM layer, Computer layer, Memory, Validation, Tracing, Agents
- Used by: Terminal Interface, SDK, External callers

**LLM Layer (`interpreter/core/llm/`):**
- Purpose: Abstraction over language models via LiteLLM
- Location: `interpreter/core/llm/llm.py`
- Contains: Model configuration, context window management, vision/function detection
- Depends on: LiteLLM, tokentrim
- Used by: Interpreter Core, Agents

**Computer Layer (`interpreter/core/computer/`):**
- Purpose: System interface for code execution and OS control
- Location: `interpreter/core/computer/computer.py`
- Contains: Terminal, Mouse, Keyboard, Display, Browser, Files, etc.
- Depends on: Language runtimes (Python, Shell, JS, etc.)
- Used by: Interpreter Core for code execution

**Response Layer (`interpreter/core/respond.py`):**
- Purpose: Main execution loop - runs LLM, handles code, manages hooks
- Location: `interpreter/core/respond.py`
- Contains: Agent routing, code execution, validation hooks, semantic memory recording
- Depends on: LLM, Computer, Agents, Memory, Validation, Tracing
- Used by: Interpreter Core via `chat()` method

**Agent Layer (`interpreter/core/agents/`):**
- Purpose: Multi-agent orchestration for complex tasks
- Location: `interpreter/core/agents/`
- Contains: `AgentOrchestrator`, `ScoutAgent`, `SurgeonAgent`, `ArchitectAgent`, `ValidatorAgent`
- Depends on: Interpreter Core, Memory
- Used by: Response layer for task routing

**Terminal Interface (`interpreter/terminal_interface/`):**
- Purpose: Interactive CLI with Rich/prompt_toolkit UI
- Location: `interpreter/terminal_interface/terminal_interface.py`
- Contains: Input handling, event subscriptions, UI components
- Depends on: Interpreter Core, Event Bus, UI Components
- Used by: CLI entry point

**SDK Layer (`interpreter/sdk/`):**
- Purpose: Tools for building custom agents
- Location: `interpreter/sdk/`
- Contains: `AgentBuilder`, `MCPBridge`, `PluginRegistry`
- Depends on: Interpreter Core
- Used by: External developers

## Data Flow

**User Message to Response:**

1. User input received via `terminal_interface.py` or `chat()` API
2. Message added to `interpreter.messages` list
3. `respond()` generator called, yields chunks
4. Agent orchestrator routes to specialized agents if applicable
5. LLM generates response via `llm.run()`
6. Code blocks detected and executed via `computer.run()`
7. Validation, tracing, memory hooks fire post-execution
8. Chunks streamed back through generator to UI

**Event Flow (UI Architecture):**

1. `respond()` or agents emit `UIEvent` via `EventBus.emit()`
2. `terminal_interface` subscribes to event types
3. Handler updates `UIState` (agents, tokens, mode)
4. UI components (AgentStrip, ToastManager, etc.) render from state

**Agent Workflow:**

1. `AgentOrchestrator._detect_workflow()` classifies task via LLM
2. Workflow type selected: NONE, EXPLORE, EDIT, FULL, VALIDATE
3. Agents executed in sequence with context passing
4. Results aggregated into `WorkflowResult`
5. Context injected into `interpreter.messages` for main LLM

**State Management:**
- `interpreter.messages`: Conversation history (list of LMC message dicts)
- `UIState`: UI-layer state (agents, tokens, mode, panels)
- `SemanticEditGraph`: Persistent edit history (DuckDB/SQLite)
- Settings: JSON file at `~/.config/open-interpreter/settings.json`

## Key Abstractions

**LMC Message Format:**
- Purpose: Universal message format for interpreter
- Examples: `{"role": "user", "type": "message", "content": "..."}`
- Pattern: Dict with role, type, format, content keys

**Chunk Generator:**
- Purpose: Streaming response format
- Examples: Yielded from `respond()`, consumed by terminal interface
- Pattern: Dict with role, type, start/end flags, content

**BaseAgent (`interpreter/core/agents/base_agent.py`):**
- Purpose: Abstract base for specialized agents
- Examples: `ScoutAgent`, `SurgeonAgent`, `ArchitectAgent`, `ValidatorAgent`
- Pattern: `run(task, context)` returns `AgentResult`

**UIEvent (`interpreter/terminal_interface/components/ui_events.py`):**
- Purpose: Typed event for UI communication
- Examples: `EventType.AGENT_SPAWN`, `EventType.CODE_START`
- Pattern: Dataclass with type, data dict, timestamp, source

**Edit (`interpreter/core/memory/edit_record.py`):**
- Purpose: Represents a code change for semantic memory
- Examples: File modifications tracked with context
- Pattern: Dataclass with file_path, content, edit_type, context

## Entry Points

**CLI Entry (`interpreter/terminal_interface/start_terminal_interface.py`):**
- Location: `interpreter/terminal_interface/start_terminal_interface.py`
- Triggers: `poetry run interpreter` command
- Responsibilities: Parse args, apply profile, start UI, run chat loop

**Python API:**
- Location: `interpreter/__init__.py` exports `interpreter` singleton
- Triggers: `from interpreter import interpreter`
- Responsibilities: Expose `OpenInterpreter` instance

**OS Mode Entry:**
- Location: `interpreter/__init__.py` (when `--os` flag)
- Triggers: `interpreter --os`
- Responsibilities: Run computer_use async loop

**Server Entry:**
- Location: `interpreter/core/async_core.py`
- Triggers: `interpreter --server`
- Responsibilities: Run `AsyncInterpreter` with HTTP server

## Error Handling

**Strategy:** Non-blocking with graceful degradation

**Patterns:**
- Feature hooks wrapped in try/except, log and continue on failure
- `UIErrorContext` context manager for UI component errors
- Generator-based streaming allows partial responses on error
- Git-based rollback via `EditRollback` for failed file changes

## Cross-Cutting Concerns

**Logging:** Python `logging` module, debug logs to `~/.open-interpreter/logs/` when `OI_UI_DEBUG=true`

**Validation:** Pre-execution syntax checking, post-execution test discovery (when `enable_validation=True`)

**Authentication:** Delegated to LiteLLM (API keys via env vars or `interpreter.llm.api_key`)

**Thread Safety:** `threading.RLock` for lazy property access, `threading.Lock` for model switching in orchestrator

**Feature Flags:** Controlled via `interpreter.enable_*` attributes, persisted to `settings.json`

---

*Architecture analysis: 2026-01-19*
