# Architecture

## Module Overview

```
interpreter/
├── core/
│   ├── core.py          # Main OpenInterpreter class
│   ├── memory/          # Semantic edit tracking
│   ├── tracing/         # Execution tracing
│   ├── agents/          # Multi-agent orchestration
│   └── validation/      # Edit validation
└── sdk/                 # Developer API
```

## Activation

Features are disabled by default. Enable via:

```bash
# Environment variable (recommended for shell aliases)
export OI_ACTIVATE_ALL=true
```

```python
# Programmatic
from interpreter import interpreter
interpreter.activate_all_features()

# Or individually
interpreter.enable_semantic_memory = True
interpreter.enable_validation = True
interpreter.enable_tracing = True
interpreter.enable_agents = True
interpreter.enable_auto_test = True
interpreter.enable_trace_feedback = True
```

Hooks in `respond.py` (execution loop):
- Pre-execution: syntax validation, file state snapshot
- Wrap execution: tracing
- Post-execution: file change detection, semantic memory recording, auto-test, trace feedback

## Core Modules

### Memory (`interpreter/core/memory/`)

Tracks code edits with semantic context.

```python
from interpreter.core.memory import SemanticEditGraph, Edit, EditType

graph = SemanticEditGraph(db_path=".edit_history.db")
graph.record_edit(Edit(
    file_path="src/api.py",
    original_content=old_code,
    new_content=new_code,
    edit_type=EditType.BUG_FIX,
))

# Query institutional knowledge
context = graph.get_institutional_knowledge("src/api.py")
```

Components:
- `SemanticEditGraph` - DuckDB/SQLite storage
- `PythonSymbolExtractor` - AST-based symbol extraction
- `ConversationLinker` - Maps edits to conversations

### Tracing (`interpreter/core/tracing/`)

Captures runtime execution for informed edits.

```python
from interpreter.core.tracing import ExecutionTracer

tracer = ExecutionTracer()
trace = tracer.trace_code("result = process_data(input)")
context = trace.to_context_string()  # LLM-readable
```

Components:
- `ExecutionTracer` - sys.settrace wrapper
- `CallGraph` - Function call relationships
- `TraceContext` - Formats traces for LLM

### Agents (`interpreter/core/agents/`)

Specialized agents for different tasks.

```python
from interpreter.core.agents import AgentOrchestrator, WorkflowType

orchestrator = AgentOrchestrator(project_root="/path/to/project")
result = orchestrator.handle_task(
    "Fix the authentication bug in login.py",
    workflow=WorkflowType.BUG_FIX,
)
```

Agents:
- `ScoutAgent` - File/symbol search (no LLM)
- `SurgeonAgent` - Precise code editing
- `AgentOrchestrator` - Coordinates workflows, emits UI events

Workflows:
- `BUG_FIX` - Scout → Surgeon
- `FEATURE` - Scout → Surgeon
- `REFACTOR` - Scout → Surgeon
- `EXPLORE` - Scout only

Event Integration:
- Emits `AGENT_SPAWN`, `AGENT_COMPLETE`, `AGENT_ERROR` via EventBus
- Tracks parent-child relationships with `parent_id`
- Helper: `_execute_agent_with_events()` for consistent event emission

### Validation (`interpreter/core/validation/`)

Validates edits before applying.

```python
from interpreter.core.validation import EditValidator

validator = EditValidator(project_root="/path/to/project")
result = validator.validate_edit(
    file_path="src/module.py",
    original_content=old_code,
    new_content=new_code,
)

if result.valid:
    # Apply edit
else:
    print(result.to_context_string())
```

Components:
- `SyntaxChecker` - Python, JS, TS, JSON, shell
- `TestDiscovery` - Finds related tests
- `EditRollback` - Git-based rollback
- `TransactionalEdit` - Context manager for safe edits

## SDK (`interpreter/sdk/`)

Developer API for building custom agents.

### AgentBuilder

```python
from interpreter.sdk import AgentBuilder

builder = AgentBuilder()

# From template
scout = builder.from_template("scout")
surgeon = builder.from_template("surgeon")

# Custom
reviewer = builder.create_agent(
    name="reviewer",
    system_prompt="You review code for bugs...",
    tools=["read", "grep"],
)

# Swarm
swarm = builder.create_swarm(
    agents=[scout, surgeon],
    orchestrator=SequentialOrchestrator(),
)
result = swarm.execute_sync("Fix the login bug")
```

Templates: `scout`, `architect`, `surgeon`, `reviewer`, `tester`

### Plugins

```python
from interpreter.sdk import AgentPlugin

class LoggingPlugin(AgentPlugin):
    async def on_before_execute(self, agent, task):
        print(f"Starting: {task}")
        return task

    async def on_after_execute(self, agent, result):
        print(f"Done: {result.success}")
        return result
```

Hooks: `on_before_execute`, `on_after_execute`, `on_before_llm`, `on_after_llm`, `on_before_edit`, `on_after_edit`, `on_error`, `on_tool_call`

Built-in: `LoggingPlugin`, `MetricsPlugin`, `ValidationPlugin`, `MemoryPlugin`, `RateLimitPlugin`

### MCP Bridge

```python
from interpreter.sdk import MCPBridge, MCPServer

bridge = MCPBridge()

# Consume MCP tools
await bridge.connect_server(MCPServer(
    name="filesystem",
    command="npx",
    args=["-y", "@anthropic/mcp-filesystem"],
))
tools = bridge.get_all_tools()
result = await bridge.call_tool("filesystem", "read_file", {"path": "/etc/hosts"})

# Expose agent as MCP server
bridge.register_agent(my_agent)
await bridge.start_stdio_server()
```

## Data Flow

```
User Request
    ↓
AgentOrchestrator
    ↓
┌─────────────────────────────────────┐
│  ScoutAgent (explore codebase)      │
│       ↓                             │
│  SurgeonAgent (propose edits)       │
│       ↓                             │
│  EditValidator (validate syntax)    │
│       ↓                             │
│  TestDiscovery (run related tests)  │
│       ↓                             │
│  SemanticEditGraph (record edit)    │
└─────────────────────────────────────┘
    ↓
Result
```

## Terminal Interface (`interpreter/terminal_interface/`)

Visual components for the CLI.

```
terminal_interface/
├── components/
│   ├── ui_state.py        # Centralized state (UIState, UIMode, AgentState)
│   ├── ui_events.py       # Event system (UIEvent, EventType, EventBus)
│   ├── ui_backend.py      # Backend abstraction (Rich/prompt_toolkit)
│   ├── sanitizer.py       # Terminal escape sequence security
│   ├── pt_app.py          # prompt_toolkit Application (Phase 1)
│   ├── input_handler.py   # Key bindings + input session
│   ├── completers.py      # Auto-completion (magic, paths, history)
│   ├── agent_strip.py     # Bottom bar with agent status (Phase 2)
│   ├── agent_tree.py      # Hierarchical agent view (Phase 2)
│   ├── context_meter.py   # Token usage progress bar (Phase 2)
│   ├── context_panel.py   # Variables/functions/metrics sidebar (Phase 3)
│   ├── code_navigator.py  # Block navigation + fold/unfold (Phase 3)
│   ├── ui_mode_manager.py # Mode state machine + auto-escalation (Phase 4)
│   ├── toast.py           # Ephemeral notifications (Phase 4)
│   ├── theme.py           # Color palette, icons
│   ├── base_block.py      # Shared console, timing
│   ├── message_block.py   # Role icons, styled panels
│   ├── code_block.py      # Language badges, status
│   ├── live_output_panel.py  # Contained output viewport
│   ├── prompt_block.py    # Styled input
│   ├── spinner_block.py   # Loading indicators
│   └── status_bar.py      # Session info + agent count + context meter
└── terminal_interface.py  # Main integration
```

### UI Architecture (Event-Driven)

```
┌──────────────┐     ┌───────────┐     ┌──────────────┐
│  Interpreter │────>│ EventBus  │────>│  UI Backend  │
│  (Generator) │     │ (queue)   │     │ (Rich/PT)    │
└──────────────┘     └─────┬─────┘     └──────────────┘
                           │
                     ┌─────┴─────┐
                     │  UIState  │
                     │(dataclass)│
                     └───────────┘
```

- **UIState** - Single source of truth for mode, agents, panels, tokens
- **EventBus** - Thread-safe queue, pub/sub handlers, rate limiting
- **UIBackend** - Abstract interface; `RichStreamBackend` (fallback), `PromptToolkitBackend` (interactive)
- **sanitizer** - Blocks dangerous escape sequences (clipboard, hyperlinks)

Components:
- `StatusBar` - Model, message count, agent count, context meter
- `MessageBlock` - Role-specific icons and borders
- `CodeBlock` - Language badges, execution status, timing, 30fps refresh throttle
- `LiveOutputPanel` - Fixed-height output viewport (prevents scroll overflow)
- `PromptBlock` - Styled prompts and confirmations
- `SpinnerBlock` - Thinking/executing animations (must stop before creating other Live blocks)
- `ErrorBlock` - Structured exception display with formatted tracebacks
- `DiffBlock` - Before/after code comparison
- `InteractiveMenu` - Arrow-key navigation for selections
- `TableDisplay` - Auto-format CSV/JSON as tables
- `NetworkStatus` - LLM request state tracking

Agent Visualization (Phase 2):
- `AgentStrip` - Bottom bar: `[Scout: ✓ 2.3s] [Surgeon: ⏳ thinking...]`
- `AgentTree` - Rich Tree with parent→child relationships, output preview
- `ContextMeter` - Token usage: `[████░░] 60% (24k/41k)` with color thresholds

Context Panel (Phase 3):
- `ContextPanel` - Variables/functions/metrics sidebar (POWER/DEBUG or when content exists)
- `CodeNavigator` - Block navigation (j/k), fold/unfold (Space), selection tracking
- `CodeBlock` (enhanced) - fold/unfold methods, is_folded property, block_id integration

Adaptive Mode System (Phase 4):
- `UIModeManager` - Score-based mode transitions with hysteresis
  - Thresholds: ZEN (0) → STANDARD (5) → POWER (15) → DEBUG (30)
  - Event scoring: AGENT_SPAWN (+10), CODE_START (+3), ERROR (+5)
  - Decay: -1 per 30s inactivity
  - Manual: `set_mode()`, `lock_mode()`, `cycle_mode()`
- `ToastManager` - Ephemeral notifications for mode changes and status
  - Levels: INFO, SUCCESS, WARNING, ERROR, MODE
  - Auto-dismiss, rate limiting, stack display (max 3)

Utilities (`terminal_interface/utils/`):
- `session_manager.py` - Autosave on interrupt, resume support
- `voice_output.py` - Cross-platform TTS (macOS/Windows/Linux)
- `ui_logger.py` - Debug logging via `UIErrorContext`

Integration points:
- `start_terminal_interface.py` - Session manager, UIBackend initialization
- `terminal_interface.py` - Event subscriptions, component wiring:
  - Initializes UIModeManager, ToastManager, AgentStrip, CodeNavigator
  - Subscribes to EventBus for AGENT_*, CODE_*, MESSAGE_* events
  - Updates UIState and displays AgentStrip during streaming
- `respond.py` - Network status (start_request, end_request, set_error)
- `orchestrator.py` - Emits AGENT_SPAWN/COMPLETE/ERROR/OUTPUT events
- `code_block.py` - Table display for tabular output detection

Performance:
- `terminal_interface.py` - 50ms refresh rate limiting during streaming
- `code_block.py` - 30fps internal throttle prevents excessive re-rendering
- `jupyter_language.py` - Thread-safe terminate() with join() before channel close

## Performance Optimizations

### LLM Processing (`interpreter/core/llm/`)
- `run_text_llm.py` - List-based string accumulation (O(n) vs O(n²) concatenation)
- `llm.py` - Set-based image filtering (O(n) vs O(n²) list removals)

### Core Loop (`interpreter/core/`)
- `respond.py` - System message caching (hash-based dependency tracking)
- `respond.py` - Reverse iteration for last code message (O(1) avg vs O(n))
- `core.py` - Thread-safe lazy module loading (double-checked locking)

## Dependencies

- DuckDB (optional, falls back to SQLite)
- pytest (for test discovery/running)
- mypy (optional, for type checking)
- Node.js (optional, for JS/TS syntax checking)
- Rich (terminal UI)
