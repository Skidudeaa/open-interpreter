# Changelog

## [Unreleased] - 2025-12-22

### Changed

- Cleaned up ~35 orphaned dependencies (chunkhound/MCP remnants, tree-sitter, pandas, quart, etc.)
- Added `duckdb` as optional `[memory]` extra in pyproject.toml
- Added `pytest-cov` to dev dependencies

### Fixed

- Terminal freeze from Rich Live context conflicts - spinner now stops before creating new blocks
- None checks in all block refresh/end methods after Live.start() fails
- Exception handlers now cleanup spinner and active_block to restore terminal state
- Replace ~40 bare `except:` with `except Exception:` across 20 files (prevents catching SystemExit/KeyboardInterrupt)
- Thread safety: UIState mutations now use threading.Lock
- try-finally in show_diff(), display_error() convenience functions
- Temp file cleanup in terminal_interface.py editor flow
- EventBus.subscribe() now idempotent (prevents handler accumulation)
- Mutable default arg in count_tokens.py
- Event queue logs dropped events instead of silent failure

#### Terminal Freeze Prevention (2025-12-22)
- Interactive menu stdin.read() timeout - prevents infinite blocking with 30s timeout per keypress
- Spinner cleanup before confirmation prompts - stops ThinkingSpinner before interactive_choice/interactive_confirm
- BaseBlock lazy Live initialization - Live display now starts on first refresh, not in __init__
- Safe console clear in interactive menu - handles errors gracefully, falls back to newlines
- Raw terminal mode always restored - tcsetattr in finally block even on exceptions

### Added

#### Terminal UI Architecture (Phase 0 + Phase 1)
- `ui_state.py` - Centralized state: `UIState`, `UIMode`, `AgentState`, `AgentRole`
- `ui_events.py` - Event system: `UIEvent`, `EventType`, `EventBus` with thread-safe queue
- `ui_backend.py` - Backend abstraction: `RichStreamBackend`, `PromptToolkitBackend`
- `sanitizer.py` - Terminal security: blocks clipboard/hyperlink escape sequences
- `pt_app.py` - prompt_toolkit Application skeleton for interactive TUI
- `input_handler.py` - Key bindings (Esc, F2, Ctrl+R, etc.) with fallbacks
- `completers.py` - Magic commands, file paths, conversation history completion
- Event emission wired into `terminal_interface.py`
- `--no-tui` flag to disable interactive mode
- prompt_toolkit dependency added

#### UI Component Integration
- Wired up 5 orphaned UI components into main flow
- Session manager: autosave on interrupt, resume prompt on startup
- Network status: LLM request lifecycle tracking in `respond.py`
- Error block: structured exception display with formatted tracebacks
- Table display: auto-detect CSV/JSON output, render as tables
- Interactive menu: arrow-key navigation for code execution confirmation
- Components exported in `components/__init__.py`

#### Agent Visualization (Phase 2)
- `agent_strip.py` - Bottom bar showing all active agents with real-time status
  - Format: `[Scout: ✓ 2.3s] [Surgeon: ⏳ thinking...] [Validator: ▶ running]`
  - Status icons: ○ pending, ⏳ running, ✓ complete, ✗ error, ⊘ cancelled
  - Color-coded by status (green/yellow/red)
  - Selection tracking for keyboard navigation
- `agent_tree.py` - Expandable hierarchical view using Rich Tree
  - Shows parent → child agent relationships
  - Displays last 3 lines of output preview for each agent
  - Color-coded status with elapsed time display
  - Support for navigation with select_next_agent/select_prev_agent
- `context_meter.py` - Token usage display with progress bar
  - Format: `[████████░░] 78% (32k/41k tokens)`
  - Color shifts green→yellow→red as context fills (60%/85% thresholds)
  - K/M suffix formatting for readability
- `orchestrator.py` - Agent event emission
  - Emits AGENT_SPAWN, AGENT_OUTPUT, AGENT_COMPLETE, AGENT_ERROR events
  - Integrates with EventBus from ui_events.py
  - Tracks parent-child agent relationships via parent_id
  - Helper method `_execute_agent_with_events()` for consistent event emission
- `status_bar.py` - Enhanced with agent count and context meter
  - Shows active agent count when agents are running
  - Integrates context meter in center section
  - Format: `💬 5 messages │ 🤖 2 agents (1 active) │ [████░] 60% (24k/41k)`

#### Context Panel (Phase 3)
- `context_panel.py` - Variables/functions/metrics sidebar
  - Shows variables with type icons and value previews
  - Shows function signatures
  - Displays execution time and memory usage
  - Adaptive visibility: POWER/DEBUG mode or when content exists
- `code_navigator.py` - Block navigation system
  - j/k navigation between code blocks
  - Space to toggle fold/unfold on output
  - Tracks fold state per block with auto-fold for long outputs (>20 lines)
  - Supports select_next_code/select_prev_code for code-only navigation
- `code_block.py` - Enhanced with fold/unfold support
  - `fold()`, `unfold()`, `toggle_fold()` methods
  - `is_folded` property for state tracking
  - Fold preview shows first 3 lines with "▶ Output (N lines, folded)" indicator
  - `block_id` and `is_selected` for CodeNavigator integration

#### Adaptive Mode System (Phase 4)
- `ui_mode_manager.py` - Mode state machine with auto-escalation
  - Score-based escalation: ZEN (0) → STANDARD (5) → POWER (15) → DEBUG (30)
  - Event scoring: AGENT_SPAWN (+10), CODE_START (+3), ERROR (+5)
  - Score decay: -1 every 30s of inactivity
  - Mode never auto-downgrades (manual only)
  - Manual controls: `set_mode()`, `lock_mode()`, `cycle_mode()`, `toggle_power_mode()`
- `toast.py` - Ephemeral notifications for mode changes
  - Toast levels: INFO, SUCCESS, WARNING, ERROR, MODE
  - Auto-dismiss with configurable timeout
  - Rate limiting (min 0.5s between toasts)
  - Stack display (max 3 visible, newest first)
  - Inline rendering for status bar integration

#### Integration
- Wired Phase 2-4 components into `terminal_interface.py`
  - Initialize UIModeManager, ToastManager, AgentStrip, CodeNavigator with shared UIState
  - Subscribe to EventBus for AGENT_SPAWN/COMPLETE/ERROR/OUTPUT events
  - Update UIState from events, trigger mode auto-escalation
  - Display AgentStrip during streaming when agents are active
  - Track code/message blocks with CodeNavigator via CODE_START/END events
- UIBackend (Rich/prompt_toolkit) created in `start_terminal_interface.py`
- Event flow: Orchestrator → EventBus → handle_agent_event() → UIState → UI Components

### Fixed
- Skip FastAPI server tests when fastapi not installed
- Fix test_generator flakiness (allow multiple console outputs)
- Fix test_activate_all_features environment isolation

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
- Enable flags: `enable_semantic_memory`, `enable_validation`, `enable_tracing`, `enable_agents`, `enable_auto_test`, `enable_trace_feedback`
- `activate_all_features()` method for quick enablement
- `OI_ACTIVATE_ALL=true` env var for automatic activation
- Hooks in `respond.py`: pre-execution validation, execution tracing, post-execution memory recording
- Lazy-loading for performance

#### File Edit Detection
- `file_snapshot.py` - Capture and diff file states before/after execution
- Detects arbitrary file modifications from executed code (not just files.edit())
- Records changes to semantic graph with full diff
- Tracks source files: .py, .js, .ts, .json, .yaml, .md, .html, .css, .sql, .sh

#### Auto-Test
- `enable_auto_test` flag - runs related tests after file modifications
- Uses `TestDiscovery` to find tests for modified files
- Reports results: ✓ passed or ✗ failed with test names
- Feeds failures to LLM with analysis options: fix now, add to todos, or continue

#### Trace Feedback
- `enable_trace_feedback` flag - feeds execution traces to LLM on failure
- Uses `TraceContextGenerator` to create LLM-readable trace context
- Automatically appends trace to conversation when code execution fails

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
- Task stopping prematurely: stricter loop breaker matching, empty LLM response detection, exponential backoff retry, graceful stop handling
- UI unresponsiveness during streaming: 50ms refresh rate limiting in terminal_interface, 30fps throttle in CodeBlock
- Jupyter kernel shutdown errors (InvalidStateError): thread-safe terminate() with join() before channel close
- Code decline exits loop instead of continuing conversation

### UI Enhancements
- Syntax-highlighted tracebacks: file paths (amber), line numbers (cyan), function names (violet)
- Stderr distinction: red coloring for error output, red border on error panels
- Code preview on undo: shows language and first 60 chars of removed code
- Progress indicator for long-running code: spinner animation after 5 seconds

#### New Components (`interpreter/terminal_interface/components/`)
- `error_block.py` - Structured error display with formatted tracebacks
- `diff_block.py` - Before/after code comparison on edit
- `interactive_menu.py` - Arrow-key navigation for selections
- `table_display.py` - Formatted tables for SQL results, CSV, JSON
- `network_status.py` - API connection state indicator

#### New Utilities (`interpreter/terminal_interface/utils/`)
- `session_manager.py` - Autosave on interrupt, session resume
- `voice_output.py` - Cross-platform TTS (macOS/Windows/Linux)
- `ui_logger.py` - Debug logging replacing silent exceptions

#### Theme System
- Multiple themes: dark (default), light, high-contrast
- Set via `OI_THEME` environment variable
- High contrast mode for accessibility

### Performance
- String accumulation in `run_text_llm.py`: O(n²) → O(n) via list-based accumulation
- Image message filtering in `llm.py`: O(n²) → O(n) via set-based filtering
- System message caching in `respond.py`: avoid per-iteration rebuilding
- Message scanning in `respond.py`: O(n) → O(1) avg via reverse iteration
- Thread-safe lazy initialization in `core.py`: double-checked locking pattern
- Debug output in `jupyter_language.py`: conditional on DEBUG_MODE flag
