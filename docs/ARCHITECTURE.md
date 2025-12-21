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
- `AgentOrchestrator` - Coordinates workflows

Workflows:
- `BUG_FIX` - Scout → Surgeon
- `FEATURE` - Scout → Surgeon
- `REFACTOR` - Scout → Surgeon
- `EXPLORE` - Scout only

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

## Dependencies

- DuckDB (optional, falls back to SQLite)
- pytest (for test discovery/running)
- mypy (optional, for type checking)
- Node.js (optional, for JS/TS syntax checking)
