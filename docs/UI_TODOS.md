# UI Enhancement Todos

## Terminal UI Architecture Overhaul

Event-driven architecture with prompt_toolkit integration.

### Phase 0 - Foundation [DONE]
- [x] `ui_state.py` - Centralized state: `UIState`, `UIMode`, `AgentState`
- [x] `ui_events.py` - Event system: `UIEvent`, `EventType`, `EventBus`
- [x] `ui_backend.py` - Backend abstraction: `RichStreamBackend`, `PromptToolkitBackend`
- [x] `sanitizer.py` - Terminal escape sequence filtering
- [x] Wire event emission into `terminal_interface.py`

### Phase 1 - prompt_toolkit Integration [DONE]
- [x] Add `prompt_toolkit` dependency
- [x] Implement `PromptToolkitBackend` with session management
- [x] Key bindings: Esc (cancel), Ctrl+R (history), Alt+P/F2 (power mode)
- [x] `pt_app.py` - Application skeleton for future full-screen mode
- [x] `input_handler.py` - Key bindings with F-key fallbacks
- [x] `completers.py` - Magic commands, file paths, conversation history
- [x] Multiline input with syntax highlighting
- [x] `--no-tui` flag to disable interactive mode

### Phase 2 - Agent Visualization [DONE]
- [x] `agent_strip.py` - Bottom bar showing agent status
  - [x] Real-time status display with icons (○ pending, ⏳ running, ✓ complete, ✗ error, ⊘ cancelled)
  - [x] Color-coded by status (green/yellow/red)
  - [x] Shows elapsed time and output preview
  - [x] Selection tracking for keyboard navigation
- [x] `agent_tree.py` - Expandable hierarchical view
  - [x] Rich Tree structure showing parent → child relationships
  - [x] Last 3 lines of output preview per agent
  - [x] Navigation support with select_next_agent/select_prev_agent
- [x] Hook into `AgentOrchestrator` for spawn/complete events
  - [x] Emits AGENT_SPAWN, AGENT_OUTPUT, AGENT_COMPLETE, AGENT_ERROR events
  - [x] Integrates with EventBus from ui_events.py
  - [x] Tracks parent-child relationships via parent_id
  - [x] Helper method `_execute_agent_with_events()` for consistent emission
- [x] Context window meter (token usage display)
  - [x] `context_meter.py` - Progress bar with percentage
  - [x] Color shifts green→yellow→red (60%/85% thresholds)
  - [x] K/M suffix formatting for readability
  - [x] Integrated into status_bar.py center section

### Phase 3 - Context Panel [DONE]
- [x] `context_panel.py` - Variables/functions/metrics sidebar
  - [x] Shows variables with type icons (🔢 int, 📝 str, 📊 DataFrame, etc.)
  - [x] Shows function signatures
  - [x] Displays execution time (⏱️) and memory usage (💾)
  - [x] Truncates long values with "..." preview
- [x] Adaptive visibility based on content
  - [x] Always visible in POWER/DEBUG mode
  - [x] Auto-shows when variables/functions exist
  - [x] Can be toggled with Alt+H / F3
- [x] Code block fold/unfold and navigation
  - [x] `code_navigator.py` - Block navigation (j/k, Space to fold)
  - [x] `code_block.py` - fold/unfold methods, is_folded property
  - [x] Auto-fold for outputs > 20 lines
  - [x] Preview shows first 3 lines when folded

### Phase 4 - Adaptive Mode System [DONE]
- [x] `ui_mode_manager.py` - Mode state machine with auto-escalation
  - [x] Score-based escalation: ZEN (0) → STANDARD (5) → POWER (15) → DEBUG (30)
  - [x] Event scoring: AGENT_SPAWN (+10), CODE_START (+3), ERROR (+5)
  - [x] Score decay: -1 every 30s of inactivity
  - [x] Manual mode control: set_mode(), lock_mode(), cycle_mode()
  - [x] Mode never auto-downgrades (manual only)
- [x] Toast notifications on mode changes
  - [x] `toast.py` - ToastManager with levels (INFO, SUCCESS, WARNING, ERROR, MODE)
  - [x] Auto-dismiss after timeout (configurable)
  - [x] Rate limiting to prevent spam
  - [x] Stack display (newest first, max 3 visible)
  - [x] Inline rendering for status bar integration

### Integration [DONE]
- [x] Wire Phase 2-4 components into `terminal_interface.py`
  - [x] Initialize UIModeManager, ToastManager, AgentStrip, CodeNavigator
  - [x] Subscribe to EventBus for AGENT_*, CODE_*, MESSAGE_* events
  - [x] Update UIState from events (agent status, token counts)
  - [x] Display AgentStrip during streaming when agents active
  - [x] Track code/message blocks with CodeNavigator
  - [x] Auto-escalate mode via UIModeManager.process_event()
- [x] UIBackend created in `start_terminal_interface.py`

---

## Bug Fixes

- [x] **Rich Live Context Conflict** - Two Live contexts (spinner + block) caused terminal freeze
  - Stop spinner on `start` chunks before creating new blocks
  - Add None checks in all block `refresh()`/`end()` methods
  - Cleanup spinner/active_block in exception handlers

## Completed - Previous Iteration

All items completed and integrated into the main codebase.

## Integration Summary

All UI components are now wired into the terminal interface:

- **Session Manager** → `start_terminal_interface.py` (autosave on interrupt)
- **Network Status** → `respond.py` (LLM request tracking)
- **Error Block** → `terminal_interface.py` (structured exception display)
- **Table Display** → `code_block.py` (auto-detect tabular output)
- **Interactive Menu** → `terminal_interface.py` (arrow-key confirmations)
- **Components exported** in `components/__init__.py`

## Completed - High Impact

- [x] **Structured Error Display** - `error_block.py` - Red-bordered error panels with formatted tracebacks
- [x] **Keyboard Shortcuts** - `interactive_menu.py` - Arrow key navigation, Ctrl+L clear
- [x] **Code Diff Display** - `diff_block.py` - Before/after comparison when editing code
- [x] **Output Pagination** - `code_block.py` - Navigate long outputs with page methods

## Completed - Medium Impact

- [x] **Network Status Indicator** - `network_status.py` - API connection state, retry attempts
- [x] **Interactive Menus** - `interactive_menu.py` - Arrow-key selection for confirmations
- [x] **Cross-Platform Voice** - `voice_output.py` - pyttsx3/espeak fallback for non-macOS

## Completed - Polish

- [x] **Theme Customization** - `theme.py` - Light mode, dark mode, custom color schemes
- [x] **High Contrast Mode** - `theme.py` - Accessibility theme with maximum contrast
- [x] **Session Autosave** - `session_manager.py` - Save on interrupt, resume support
- [x] **Table Display** - `table_display.py` - Format SQL results, CSV, JSON as tables

## Completed - Code Quality

- [x] **Replace Silent Exceptions** - `ui_logger.py` - Debug logging via `UIErrorContext`

## Usage

### Theme Selection
```bash
# Set theme via environment variable
export OI_THEME=light      # Light mode
export OI_THEME=dark       # Dark mode (default)
export OI_THEME=high-contrast  # High contrast for accessibility
```

### Debug Logging
```bash
# Enable UI debug logging
export OI_UI_DEBUG=true
# Logs written to ~/.open-interpreter/logs/ui_debug.log
```

### Session Management
Sessions are automatically saved to `~/.open-interpreter/sessions/` on interrupt.
