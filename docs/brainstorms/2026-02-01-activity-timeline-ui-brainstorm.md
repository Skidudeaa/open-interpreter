# Activity Timeline UI

**Date:** 2026-02-01
**Status:** Ready for planning

## What We're Building

A new default terminal UI layout that emphasizes real-time activity over raw code output:

1. **Main area**: Vertical activity timeline showing execution flow as connected nodes
2. **Right sidebar**: Collapsible code panel that streams code during execution
3. **Bottom bar**: Token/resource usage indicators

### Timeline Node Types

| Icon | Activity | Example Content |
|------|----------|-----------------|
| ○ | Agent spawn | "Scout started" |
| ├─ | Agent action | "Reading config.py", "Found 3 functions" |
| ✓ | Success | "Tests passed (5/5)", "Validation passed" |
| ✗ | Error | "SyntaxError in line 45" |
| ▶ | Execution | "Running line 45", "Call depth: 3" |
| 💾 | File change | "Modified utils.py (+12, -3)" |
| 📊 | Metrics | "Tokens: 1.2k/128k" |

### Layout

```
┌─────────────────────────────────────────┬──────────────────┐
│ Activity Timeline (scrollable)          │ Code Panel       │
│                                         │ (collapsible)    │
│ ○ Scout started                    0.0s │                  │
│ ├─ Reading config.py               0.2s │ ▼ python         │
│ ├─ Found 3 functions               0.8s │ ```              │
│ ○ Surgeon started                  1.1s │ def setup():     │
│ ├─ ▶ Executing fix                 1.5s │   config = {}    │
│ ├─ 💾 utils.py (+5, -2)            1.8s │   return config  │
│ ├─ ✓ Validation passed             2.1s │ ```              │
│ └─ ✓ Tests passed (5/5)            2.8s │                  │
│ ○ Complete                         3.2s │ [Toggle: Alt+C]  │
├─────────────────────────────────────────┴──────────────────┤
│ Tokens: 1.2k ▮▮▮░░░░░ 28%  │  Memory: 45MB  │  Time: 3.2s  │
└─────────────────────────────────────────────────────────────┘
```

## Why This Approach

**Timeline over log stream:**
- Shows hierarchical relationships (agent → sub-actions)
- Visual flow is easier to scan than wall of text
- Timing on right side keeps content clean

**Code in sidebar over inline:**
- Reduces cognitive load during execution
- Code is still accessible but not dominant
- User can expand/collapse as needed

**Default for all modes:**
- Consistent experience regardless of complexity
- ZEN mode could hide sidebar, DEBUG could add raw chunks panel

## Key Decisions

1. **Timeline as main view** - Activity flow is primary, code is secondary
2. **Hierarchical nesting** - Agent actions nest under agent spawn nodes
3. **Elapsed time, not timestamps** - "2.1s" cleaner than "12:34:07"
4. **Code sidebar collapsible** - Alt+C toggle, remembers state
5. **Resource bar at bottom** - Always visible, compact

## Activity Types to Display

### Agent Lifecycle
- `AGENT_SPAWN` → "○ {role} started"
- `AGENT_OUTPUT` → "├─ {summary}"
- `AGENT_COMPLETE` → "└─ ✓ {role} complete ({elapsed})"
- `AGENT_ERROR` → "└─ ✗ {error_summary}"

### Code Execution
- `CODE_START` → "├─ ▶ Running {language}"
- `CONSOLE_ACTIVE_LINE` → Update execution indicator
- `CONSOLE_OUTPUT` → Summarize or show last line
- `CODE_END` → "├─ ✓ Executed ({duration})"

### Validation & Testing
- `VALIDATION_START` → "├─ Validating..."
- `VALIDATION_END` → "├─ ✓ Valid" or "├─ ✗ {error_count} errors"
- `TEST_START` → "├─ Running tests..."
- `TEST_END` → "├─ ✓ {passed}/{total} passed"

### File Changes
- `FILE_CHANGE` → "├─ 💾 {filename} (+{added}, -{removed})"
- `GIT_COMMIT` → "├─ 📦 Committed {short_hash}"

### Resources (bottom bar updates)
- `SYSTEM_TOKEN_UPDATE` → Update token progress bar
- Context panel metrics → Update memory/time

## Open Questions

1. **Scroll behavior**: Auto-scroll during execution, pause on user interaction?
2. **Node expansion**: Click to expand details, or just hover?
3. **Code panel width**: Fixed 30%, or user-resizable?
4. **Color scheme**: Match existing theme, or distinct activity colors?

## Technical Notes

### Implementation Path

1. Create `ActivityTimelineWidget` (Textual widget)
2. Create `CodeSidebarWidget` (collapsible panel)
3. Modify `textual_app.py` layout to use new widgets
4. Subscribe timeline to EventBus events
5. Route code chunks to sidebar instead of main output
6. Add toggle keybinding (Alt+C for code panel)
7. Update mode manager to show/hide components

### Existing Components to Leverage

- `ActivityStream` - Has activity type icons, can adapt
- `CodeBlockWidget` - Syntax highlighting, use in sidebar
- `EventBus` - Already emits all needed events
- `UIState` - Track panel visibility, scroll position

## Next Steps

Run `/workflows:plan` to create implementation plan.
