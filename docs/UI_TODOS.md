# UI Enhancement Todos

## Terminal UI Architecture Overhaul

Event-driven architecture with prompt_toolkit integration.

### Phase 0 - Foundation [DONE]
- [x] `ui_state.py` - Centralized state: `UIState`, `UIMode`, `AgentState`
- [x] `ui_events.py` - Event system: `UIEvent`, `EventType`, `EventBus`
- [x] `ui_backend.py` - Backend abstraction: `RichStreamBackend`, `PromptToolkitBackend`
- [x] `sanitizer.py` - Terminal escape sequence filtering
- [x] Wire event emission into `terminal_interface.py`

### Phase 1 - prompt_toolkit Integration [TODO]
- [ ] Add `prompt_toolkit` dependency
- [ ] Implement `PromptToolkitBackend` with always-on Application
- [ ] Key bindings: Esc (cancel), Ctrl+R (history), Alt+P/F2 (power mode)
- [ ] Rich → ANSI → prompt_toolkit rendering bridge
- [ ] Multiline input with syntax highlighting

### Phase 2 - Agent Visualization [TODO]
- [ ] `agent_strip.py` - Bottom bar showing agent status
- [ ] `agent_tree.py` - Expandable hierarchical view
- [ ] Hook into `AgentOrchestrator` for spawn/complete events
- [ ] Context window meter (token usage display)

### Phase 3 - Context Panel [TODO]
- [ ] `context_panel.py` - Variables/functions/metrics sidebar
- [ ] Adaptive visibility based on content
- [ ] Code block fold/unfold and navigation

### Phase 4 - Adaptive Mode System [TODO]
- [ ] `ui_mode_manager.py` - Mode state machine with auto-escalation
- [ ] Modes: ZEN → STANDARD → POWER → DEBUG
- [ ] Toast notifications on mode changes

---

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
