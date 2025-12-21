# Changelog

## [Unreleased] - 2025-12-21

### Added

#### Semantic Edit Graph (`interpreter/core/memory/`)
- `SemanticEditGraph` - DuckDB/SQLite backed storage for code edits
- `PythonSymbolExtractor` - AST-based symbol extraction
- `ConversationLinker` - Links edits to conversation context
- Tracks WHY code changed, not just what

#### Execution Tracing (`interpreter/core/tracing/`)
- `ExecutionTracer` - Python sys.settrace based call graph tracer
- `TraceContext` - Converts execution traces to LLM-readable context
- `CallGraph` - Data structures for function call relationships

#### Multi-Agent Orchestration (`interpreter/core/agents/`)
- `AgentOrchestrator` - Coordinates specialized agents
- `ScoutAgent` - Codebase exploration (no LLM required)
- `SurgeonAgent` - Precise code editing with proposals
- `WorkflowType` - BUG_FIX, FEATURE, REFACTOR, EXPLORE workflows

#### Edit Validation (`interpreter/core/validation/`)
- `EditValidator` - Validates edits before applying
- `SyntaxChecker` - Multi-language (Python, JS, TS, JSON, shell)
- `TestDiscovery` - Finds and runs related tests
- `EditRollback` - Git-based rollback mechanism
- No Docker required - uses temp files + subprocess isolation

#### SDK Layer (`interpreter/sdk/`)
- `AgentBuilder` - Factory for custom agents with templates
- `Agent` - Configurable agent with memory and plugins
- `Swarm` - Multi-agent coordination
- `SequentialOrchestrator`, `ParallelOrchestrator`, `PipelineOrchestrator`
- `AgentPlugin` - Hook-based extension system
- `MCPBridge` - Model Context Protocol integration
- Built-in plugins: Logging, Metrics, Validation, Memory, RateLimit

#### Core Integration
- `semantic_graph` and `conversation_linker` properties on OpenInterpreter
- `validator`, `syntax_checker`, `tracer`, `agent_orchestrator` properties
- Enable flags: `enable_semantic_memory`, `enable_validation`, `enable_tracing`, `enable_agents`
- `activate_all_features()` method for quick enablement
- `OI_ACTIVATE_ALL=true` env var for automatic activation
- Hooks in `respond.py`: pre-execution validation, execution tracing, post-execution memory recording
- Lazy-loading for performance

#### Terminal UI (`interpreter/terminal_interface/components/`)
- `theme.py` - Cyber Professional color palette (violet/cyan/slate)
- `status_bar.py` - Model/session/mode display
- `prompt_block.py` - Styled input prompts
- `spinner_block.py` - Thinking/executing spinners
- `live_output_panel.py` - Contained output viewport (fixes scrolling)
- Redesigned `message_block.py` - Role icons, colored borders
- Redesigned `code_block.py` - Language badges, status indicators, timing

### Changed
- Risk-based approval system for dangerous commands
- Terminal UI uses Rich-based styled components

### Fixed
- Silent exception swallowing in computer_use loop
- Debug prints in production code
- Hardcoded sleeps in computer_use module
