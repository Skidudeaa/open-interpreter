# Open Interpreter Terminal UI Enhancement Plan

## Executive Summary

Enhance the existing Rich-based terminal UI with interactive capabilities using a **"Rich++ with Progressive Textual"** approach - staying within the Rich ecosystem for most enhancements while selectively adopting Textual for complex interactive widgets.

## User Priorities (Confirmed)
- **Phase 1 First**: Key bindings + full prompt_toolkit integration
- **Adaptive UI**: Complexity expands organically based on task
- **Agent Visualization**: Critical - elevated to Phase 2
- **Input Enhancement**: Full prompt_toolkit with multiline, suggestions, syntax highlighting

---

## Current State Analysis

**Existing Stack:**
- Python 3.9+ with Rich v13.4.2
- Component architecture: `MessageBlock`, `CodeBlock`, `ErrorBlock`, `SpinnerBlock`, etc.
- 3 built-in themes (Dark, Light, High-Contrast)
- Streaming output with Live displays
- No keyboard input handling beyond basic readline

**Key Files:**
- `/root/open-interpreter-fork/interpreter/terminal_interface/terminal_interface.py` - Main UI loop
- `/root/open-interpreter-fork/interpreter/terminal_interface/components/` - All visual blocks
- `/root/open-interpreter-fork/interpreter/terminal_interface/components/theme.py` - Theming system

---

## Recommended Approach: Hybrid "Rich++"

### Why Not Full Textual Migration?
- Would require major refactor of streaming architecture
- Current Rich components are mature and well-tested
- Risk of regression in core functionality
- Overkill for incremental improvements

### Why Not Pure Rich?
- No native keyboard input handling
- Limited interactivity without extensions
- Can't easily implement features like collapsible panels or navigation

### The Hybrid Solution:
1. **Keep Rich** for all rendering and streaming output
2. **Add `rich_interactive`** for keyboard-navigable elements
3. **Add `prompt_toolkit`** for enhanced input and key bindings
4. **Selectively use Textual** for complex widgets (optional phase 2)

---

## Implementation Phases (Reordered by Priority)

### Phase 1: Enhanced Input & Key Bindings (PRIORITY - Foundation)

**Goal:** Full prompt_toolkit integration with keyboard shortcuts and rich input experience

**Dependencies to add:**
```toml
prompt_toolkit = "^3.0.0"
# rich_interactive = "^0.1.0"  # Evaluate stability first
```

**Features:**
1. **Full Prompt Toolkit Input**
   - Multiline editing with Python/natural language syntax awareness
   - Auto-suggestions from conversation history
   - Tab completion for file paths, commands, variables
   - Syntax highlighting in the input prompt itself
   - Vi/Emacs keybinding modes (user preference)

2. **Global Key Bindings**
   - `Ctrl+L` - Clear screen
   - `Alt+P` - Toggle power/debug mode
   - `Alt+H` - Show/hide context sidebar
   - `Alt+A` - Focus agent status strip
   - `Esc` - Cancel current operation / close panels
   - `Ctrl+R` - Search conversation history
   - `Ctrl+Space` - Trigger auto-complete

3. **Command Palette**
   - `/` prefix triggers fuzzy command search
   - Shows available magic commands with descriptions
   - Recent commands at top

**Files to modify:**
- `terminal_interface.py` - Replace input() with prompt_toolkit session
- `start_terminal_interface.py` - Initialize prompt_toolkit app
- New: `components/input_handler.py` - Key binding management + input session
- New: `components/command_palette.py` - Fuzzy command search widget
- New: `components/history_completer.py` - Conversation-aware completions

---

### Phase 2: Agent Orchestration View (CRITICAL - User Priority)

**Goal:** Visualize and manage background agents - essential for multi-agent workflows

**Features:**
1. **Agent Status Strip** (Always visible when agents running)
   - Persistent bottom bar showing all active agents
   - Format: `[Agent 1: ✓ done] [Agent 2: ⏳ thinking...] [Agent 3: ▶ running]`
   - `Alt+A` to focus, arrow keys to navigate
   - `Enter` to expand agent output, `K` to kill agent
   - Auto-appears when first agent spawns, auto-hides when all complete

2. **Agent Tree Widget** (Expandable from strip)
   - Hierarchical view: parent → child agents
   - Status indicators with timing: `⏳ Agent 1 (12.3s)`
   - Collapsible output preview (last 3 lines)
   - Color-coded: green=complete, yellow=running, red=error, gray=pending

3. **Context Window Meter** (In status bar)
   - Visual progress bar: `[████████░░] 78%`
   - Color shifts: green→yellow→red as context fills
   - Tooltip shows: "32,000 / 41,000 tokens used"

**Files to create/modify:**
- New: `components/agent_strip.py` - Bottom agent status bar
- New: `components/agent_tree.py` - Expandable tree view
- `components/status_bar.py` - Add context window meter
- `terminal_interface.py` - Hook into agent spawn/complete events

**Integration with interpreter.core:**
```python
# Hook into agent lifecycle
interpreter.on_agent_spawn = lambda agent: agent_strip.add(agent)
interpreter.on_agent_complete = lambda agent: agent_strip.update(agent)
interpreter.on_agent_output = lambda agent, chunk: agent_strip.preview(agent, chunk)
```

---

### Phase 3: Contextual Zones & Dynamic Panels

**Goal:** Add collapsible/expandable information panels that appear contextually

**Adaptive UI Principle:** Panels appear when relevant, not always visible
- Variable inspector appears when you define variables
- Function panel appears when you create functions
- Metrics panel appears during long-running operations

**Features:**
1. **Smart Context Panel** (Right side, collapsible)
   - **Variables Section**: Live-updating list of defined variables with types/values
   - **Functions Section**: Recently defined functions with signatures
   - **Metrics Section**: Execution time, memory, API calls
   - Appears automatically when content exists, `Alt+H` to toggle

2. **Code Block Enhancements**
   - Collapsible output sections (click line count to expand)
   - Line-by-line navigation during code review
   - Inline diff highlighting for edits

3. **Status Bar Enhancement**
   - Already has model/mode info
   - Add: message count, session duration
   - Make sections clickable to drill down

**Files to modify:**
- New: `components/context_panel.py` - Variable/function/metrics inspector
- `components/code_block.py` - Add collapsible sections, navigation
- `components/status_bar.py` - Add interactive elements

**Adaptive Visibility Logic:**
```python
class ContextPanel:
    def should_show(self) -> bool:
        # Show if we have interesting content
        return (
            len(self.variables) > 0 or
            len(self.functions) > 0 or
            self.interpreter.ui_mode == 'power'
        )
```

---

### Phase 4: Adaptive UI Mode System

**Goal:** UI complexity adapts organically to task, with manual override available

**Adaptive Triggers** (automatic mode escalation):
| Condition | Mode Escalation |
|-----------|-----------------|
| First message | Zen (clean slate) |
| 3+ exchanges | → Standard (show status bar) |
| Agent spawned | → Show agent strip |
| Variable defined | → Show context panel hint |
| Long-running code (>5s) | → Show metrics |
| Error encountered | → Show debug info |

**Manual Overrides:**
- `Alt+P` - Toggle power mode (show everything)
- `%zen` - Force minimal mode
- `%debug` - Show all diagnostic info
- `--verbose` flag at startup

**Mode Definitions:**
| Mode | Visible Elements |
|------|-----------------|
| Zen | Conversation only, minimal chrome |
| Standard | + Status bar, + Collapsible outputs |
| Power | + Context panel, + Agent tree, + Metrics |
| Debug | + Chunk stream, + Timing, + Token counts, + Raw API |

**Implementation:**
- Add `interpreter.ui_mode` property with auto-escalation logic
- Add `interpreter.ui_complexity_score` that tracks session complexity
- Each component has `min_mode` threshold
- Smooth transitions with Rich animations

**Files to modify:**
- New: `components/ui_mode_manager.py` - Mode state machine
- `terminal_interface.py` - Mode management integration
- All `components/*.py` - Add `min_mode` property and mode-aware rendering

---

### Phase 5: Visual Polish & Micro-interactions

**Goal:** Add delightful details that make the UI feel alive

**Features:**
1. **Confidence Gradient** (experimental)
   - Vary text opacity based on token probability
   - Requires model API to expose logprobs

2. **Thinking Breadcrumbs**
   - Show collapsed reasoning snippets
   - Expand on hover/keypress

3. **Ghost Panels**
   - Dim preview of uncommitted changes
   - Show what LLM is considering

4. **Smooth Animations**
   - Panel slide-in/out
   - Progress bar fill effects
   - Cursor blink improvements

---

## Technical Implementation Details

### Key Binding System Design

```python
# components/input_handler.py
from prompt_toolkit.key_binding import KeyBindings

class UIKeyBindings:
    def __init__(self, interpreter):
        self.kb = KeyBindings()
        self.interpreter = interpreter

        @self.kb.add('escape')
        def cancel(event):
            self.interpreter.cancel_current_operation()

        @self.kb.add('alt-p')
        def toggle_power_mode(event):
            self.interpreter.toggle_ui_mode('power')

        @self.kb.add('alt-h')
        def toggle_context_panel(event):
            self.interpreter.toggle_panel('context')
```

### Layout Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ StatusBar: model | tokens | agents | mode                       │
├─────────────────────────────────────────────────────┬───────────┤
│                                                     │ Context   │
│  Main Conversation Area                             │ Panel     │
│  (MessageBlocks, CodeBlocks)                        │           │
│                                                     │ • vars    │
│                                                     │ • funcs   │
│                                                     │ • metrics │
├─────────────────────────────────────────────────────┴───────────┤
│ AgentStrip: [Agent 1: ✓] [Agent 2: ⏳] [Agent 3: ▶]             │
├─────────────────────────────────────────────────────────────────┤
│ InputPrompt: ❯ _                                                │
└─────────────────────────────────────────────────────────────────┘
```

### Mode-Aware Component Example

```python
# components/base_block.py (modified)
class BaseBlock:
    def should_render(self) -> bool:
        mode = self.interpreter.ui_mode
        if mode == 'zen':
            return self.render_in_zen_mode
        elif mode == 'debug':
            return True  # Show everything
        return True  # Standard mode

    @property
    def render_in_zen_mode(self) -> bool:
        return True  # Override in subclasses
```

---

## Dependencies & Compatibility

**New dependencies:**
```toml
[tool.poetry.dependencies]
prompt_toolkit = "^3.0.0"
# rich_interactive = "^0.1.0"  # Evaluate stability first
```

**Platform considerations:**
- All features must work on Linux, macOS, Windows
- Degrade gracefully on limited terminals (no mouse, no 256 color)
- Test on: iTerm2, Terminal.app, Windows Terminal, basic Linux TTY

---

## Risk Mitigation

1. **Streaming disruption**: Keep streaming architecture unchanged; only wrap output
2. **Performance**: Throttle UI updates to 30fps max (already in place)
3. **Compatibility**: Feature-detect terminal capabilities, disable fancy features on basic terminals
4. **Rollback**: Each phase is independent; can ship incrementally

---

## Success Metrics

- [ ] Key bindings work across all platforms
- [ ] Context panel shows useful information without cluttering
- [ ] Mode switching feels instant (<100ms)
- [ ] No regression in basic streaming performance
- [ ] Power users report improved workflow
- [ ] New users aren't overwhelmed (Zen mode works)

---

## All Files Summary

### New Files to Create:
| File | Phase | Purpose |
|------|-------|---------|
| `components/input_handler.py` | 1 | prompt_toolkit integration, key bindings |
| `components/command_palette.py` | 1 | Fuzzy command search widget |
| `components/history_completer.py` | 1 | Conversation-aware auto-complete |
| `components/agent_strip.py` | 2 | Bottom bar showing agent status |
| `components/agent_tree.py` | 2 | Expandable hierarchical agent view |
| `components/context_panel.py` | 3 | Variable/function/metrics inspector |
| `components/ui_mode_manager.py` | 4 | Adaptive mode state machine |

### Existing Files to Modify:
| File | Phases | Changes |
|------|--------|---------|
| `terminal_interface.py` | 1,2,4 | prompt_toolkit loop, agent hooks, mode management |
| `start_terminal_interface.py` | 1 | Initialize prompt_toolkit app |
| `components/status_bar.py` | 2,3 | Context meter, interactive elements |
| `components/code_block.py` | 3 | Collapsible sections, navigation |
| `components/base_block.py` | 4 | Mode-aware rendering base |
| `components/*.py` (all) | 4 | Add `min_mode` property |

### Dependencies to Add:
```toml
[tool.poetry.dependencies]
prompt_toolkit = "^3.0.0"
```

---

## Next Steps

1. **Start Phase 1**: Implement prompt_toolkit integration and key bindings
2. Add agent status strip (Phase 2) immediately after - critical for user workflow
3. Build adaptive mode system as foundation for panels
4. Polish with context panels and visual enhancements
