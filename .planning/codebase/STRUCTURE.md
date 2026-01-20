# Codebase Structure

**Analysis Date:** 2026-01-19

## Directory Layout

```
open-interpreter-fork/
├── interpreter/              # Main package
│   ├── __init__.py           # Package entry, exports interpreter singleton
│   ├── core/                 # Core interpreter logic
│   ├── terminal_interface/   # CLI and UI components
│   ├── computer_use/         # OS control mode (anthropic computer use)
│   ├── sdk/                  # Agent building SDK
│   └── codebase/             # Codebase-related utilities
├── tests/                    # Test suite
│   └── core/                 # Core module tests
├── docs/                     # Documentation (mdx format)
├── examples/                 # Usage examples
├── pyproject.toml            # Project config (Poetry)
├── CLAUDE.md                 # AI assistant instructions
└── .planning/                # Planning documents
```

## Directory Purposes

**`interpreter/core/`:**
- Purpose: Core interpreter logic and subsystems
- Contains: Main classes, LLM interface, computer interface, feature modules
- Key files:
  - `core.py` - `OpenInterpreter` class
  - `respond.py` - Main execution loop
  - `default_system_message.py` - Base system prompt
  - `render_message.py` - Message formatting

**`interpreter/core/llm/`:**
- Purpose: LLM abstraction layer
- Contains: LiteLLM wrapper, message conversion, streaming handlers
- Key files:
  - `llm.py` - `Llm` class
  - `run_text_llm.py` - Text model handler
  - `run_tool_calling_llm.py` - Function/tool calling handler
  - `utils/convert_to_openai_messages.py` - Format conversion

**`interpreter/core/computer/`:**
- Purpose: System interface for code execution
- Contains: Terminal, input devices, display, browser, files
- Key files:
  - `computer.py` - `Computer` class (aggregates all tools)
  - `terminal/terminal.py` - Code execution engine
  - `terminal/languages/*.py` - Language-specific runners

**`interpreter/core/agents/`:**
- Purpose: Multi-agent orchestration
- Contains: Specialized agents and coordinator
- Key files:
  - `orchestrator.py` - `AgentOrchestrator`, workflow routing
  - `base_agent.py` - `BaseAgent` abstract class
  - `scout_agent.py` - File/code search agent
  - `surgeon_agent.py` - Code modification agent
  - `architect_agent.py` - Structure analysis agent
  - `validator_agent.py` - Test/validation agent
  - `types.py` - Shared types (`AgentRole`, `AgentResult`)

**`interpreter/core/memory/`:**
- Purpose: Semantic memory for edit tracking
- Contains: Edit graph, symbol extraction, conversation linking
- Key files:
  - `semantic_graph.py` - `SemanticEditGraph` (DuckDB/SQLite)
  - `edit_record.py` - `Edit`, `EditType`, data structures
  - `symbol_extractor.py` - AST-based symbol extraction
  - `conversation_linker.py` - Links edits to conversation context

**`interpreter/core/validation/`:**
- Purpose: Edit validation and test discovery
- Contains: Syntax checking, test runners, rollback
- Key files:
  - `validator.py` - `EditValidator` class
  - `syntax_checker.py` - `SyntaxChecker` (AST/node checks)
  - `test_discovery.py` - `TestDiscovery` (finds related tests)
  - `rollback.py` - `EditRollback` (git-based undo)
  - `auto_commit.py` - Automatic git commits

**`interpreter/core/tracing/`:**
- Purpose: Execution tracing for debugging
- Contains: Call graphs, variable state capture
- Key files:
  - `execution_tracer.py` - `ExecutionTracer`, `ExecutionTrace`
  - `call_graph.py` - `CallGraph`, `CallNode`
  - `trace_context.py` - `TraceContextGenerator`

**`interpreter/terminal_interface/`:**
- Purpose: CLI and interactive UI
- Contains: Input handling, output rendering, UI components
- Key files:
  - `start_terminal_interface.py` - CLI entry, argument parsing
  - `terminal_interface.py` - Main UI loop
  - `magic_commands.py` - `%` command handling

**`interpreter/terminal_interface/components/`:**
- Purpose: Rich/prompt_toolkit UI building blocks
- Contains: Blocks, panels, menus, state management
- Key files:
  - `ui_events.py` - `EventBus`, `UIEvent`, `EventType`
  - `ui_state.py` - `UIState` (single source of truth)
  - `ui_backend.py` - `PromptToolkitBackend`, `RichStreamBackend`
  - `code_block.py` - `CodeBlock` (syntax-highlighted code display)
  - `message_block.py` - `MessageBlock` (markdown rendering)
  - `agent_strip.py` - `AgentStrip` (agent status display)
  - `toast.py` - `ToastManager` (notifications)
  - `spinner_block.py` - `ThinkingSpinner`

**`interpreter/sdk/`:**
- Purpose: Agent building toolkit for developers
- Contains: Agent factory, plugin system, MCP integration
- Key files:
  - `agent_builder.py` - `AgentBuilder`, `Agent`, `Swarm`
  - `plugins.py` - `AgentPlugin`, `PluginRegistry`
  - `mcp_bridge.py` - `MCPBridge` (Model Context Protocol)

**`interpreter/computer_use/`:**
- Purpose: Anthropic computer use mode
- Contains: Tool definitions for screen control
- Key files:
  - `tools/base.py` - Base tool class
  - `tools/collection.py` - Tool collection
  - `tools/edit.py` - File editing tool
  - `tools/run.py` - Command execution tool

## Key File Locations

**Entry Points:**
- `interpreter/__init__.py`: Package entry, singleton export
- `interpreter/terminal_interface/start_terminal_interface.py`: CLI main
- `interpreter/core/async_core.py`: Async/server mode

**Configuration:**
- `pyproject.toml`: Dependencies, build config
- `interpreter/terminal_interface/profiles/`: Model profiles (yaml/py)
- `~/.config/open-interpreter/settings.json`: User settings (runtime)

**Core Logic:**
- `interpreter/core/core.py`: `OpenInterpreter` class
- `interpreter/core/respond.py`: Execution loop
- `interpreter/core/llm/llm.py`: LLM interface
- `interpreter/core/computer/computer.py`: System interface

**Testing:**
- `tests/core/`: Core module tests
- `tests/test_*.py`: Test files

## Naming Conventions

**Files:**
- Snake_case for all Python files: `start_terminal_interface.py`
- `__init__.py` for package exports
- `base_*.py` for abstract base classes

**Directories:**
- Lowercase, singular: `core`, `memory`, `validation`
- Plural for collections: `agents`, `components`, `languages`

**Classes:**
- PascalCase: `OpenInterpreter`, `AgentOrchestrator`
- Suffix indicates role: `*Agent`, `*Block`, `*Manager`

**Functions:**
- snake_case: `handle_task`, `run_explore_workflow`
- Private with underscore: `_detect_workflow`, `_emit_agent_event`

**Constants:**
- UPPER_SNAKE_CASE: `_PROJECT_MARKERS`, `REFRESH_INTERVAL`

## Where to Add New Code

**New Feature:**
- Primary code: `interpreter/core/` (new module or extend existing)
- Lazy loading: Add to `core.py` with `_get_*_module()` pattern
- Tests: `tests/core/test_<feature>.py`

**New Agent Type:**
- Implementation: `interpreter/core/agents/<role>_agent.py`
- Register in: `interpreter/core/agents/orchestrator.py` (`_create_agent`)
- Export in: `interpreter/core/agents/__init__.py`

**New UI Component:**
- Implementation: `interpreter/terminal_interface/components/<name>.py`
- Event type: Add to `EventType` enum in `ui_events.py`
- Handler: Add to `terminal_interface.py` event handling

**New Language Support:**
- Implementation: `interpreter/core/computer/terminal/languages/<lang>.py`
- Inherit from: `BaseLanguage` in `base_language.py`
- Register in: `interpreter/core/computer/terminal/terminal.py`

**New Computer Tool:**
- Implementation: `interpreter/core/computer/<tool>/<tool>.py`
- Register in: `interpreter/core/computer/computer.py`

**SDK Extension:**
- Plugins: Implement `AgentPlugin` from `interpreter/sdk/plugins.py`
- MCP tools: Add to `MCPBridge` in `interpreter/sdk/mcp_bridge.py`

**Utilities:**
- Shared helpers: `interpreter/core/utils/`
- UI helpers: `interpreter/terminal_interface/utils/`

## Special Directories

**`interpreter/terminal_interface/profiles/`:**
- Purpose: Pre-configured model/behavior profiles
- Generated: No (user-editable)
- Committed: Yes (defaults directory)

**`~/.config/open-interpreter/`:**
- Purpose: User config and data
- Generated: Yes (at runtime)
- Committed: No (user-specific)

**`~/.open-interpreter/logs/`:**
- Purpose: Debug logs (when `OI_UI_DEBUG=true`)
- Generated: Yes (at runtime)
- Committed: No

**`interpreter/codebase/`:**
- Purpose: Codebase analysis utilities
- Generated: No
- Committed: Yes

**`docs/`:**
- Purpose: Documentation (MDX format)
- Generated: No
- Committed: Yes

---

*Structure analysis: 2026-01-19*
