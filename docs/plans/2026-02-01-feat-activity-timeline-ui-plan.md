---
title: "feat: Activity Timeline UI"
type: feat
date: 2026-02-01
---

# Activity Timeline UI

## Overview

Replace the code-output-heavy terminal UI with an activity-focused timeline as the default view. The main area shows a hierarchical timeline of execution events while code streams to a collapsible right sidebar. This emphasizes real-time activity visibility during execution, with code revealed primarily in final responses.

## Problem Statement

The current UI streams raw code and output directly, creating:
- Visual noise during execution (walls of code scrolling by)
- Difficulty tracking what agents are doing
- No clear sense of execution progress or timing
- Cognitive overload when multiple operations run

## Proposed Solution

A three-panel layout:

```
┌─────────────────────────────────────────┬──────────────────┐
│ Activity Timeline (scrollable)          │ Code Panel       │
│                                         │ (collapsible)    │
│ ○ Scout started                    0.0s │                  │
│ ├─ Reading config.py               0.2s │ ▼ python         │
│ ├─ Found 3 functions               0.8s │ def setup():     │
│ ○ Surgeon started                  1.1s │   config = {}    │
│ ├─ ▶ Executing fix                 1.5s │   return config  │
│ ├─ 💾 utils.py (+5, -2)            1.8s │                  │
│ ├─ ✓ Validation passed             2.1s │ [Toggle: Alt+C]  │
│ └─ ✓ Tests passed (5/5)            2.8s │                  │
│ ○ Complete                         3.2s │                  │
├─────────────────────────────────────────┴──────────────────┤
│ Tokens: 1.2k ▮▮▮░░░░░ 28%  │  Memory: 45MB  │  Time: 3.2s  │
└─────────────────────────────────────────────────────────────┘
```

## Technical Approach

### Data Model

Create `TimelineNode` dataclass in `interpreter/terminal_interface/components/timeline_state.py`:

```python
@dataclass
class TimelineNode:
    id: str
    timestamp: float
    elapsed_seconds: float = 0.0
    status: NodeStatus = NodeStatus.PENDING  # PENDING/RUNNING/COMPLETE/ERROR/CANCELLED
    node_type: NodeType  # AGENT_SPAWN/CODE_EXEC/FILE_CHANGE/VALIDATION/etc.
    icon: str = ""
    primary_text: str = ""
    secondary_text: str = ""
    parent_id: str | None = None
    code_block_id: str | None = None  # Links to sidebar code
    error_message: str | None = None
    is_expanded: bool = False
```

### Event-to-Node Mapping

| EventType | NodeType | Icon | Primary Text |
|-----------|----------|------|-------------|
| `AGENT_SPAWN` | `AGENT` | ○ | "{role} started" |
| `AGENT_OUTPUT` | `AGENT_ACTION` | ├─ | "{summary}" |
| `AGENT_COMPLETE` | `AGENT` | ✓ | "{role} complete" |
| `AGENT_ERROR` | `AGENT` | ✗ | "{error_summary}" |
| `CODE_START` | `CODE_EXEC` | ▶ | "Running {language}" |
| `CODE_END` | `CODE_EXEC` | ✓ | "Executed" |
| `FILE_CHANGE` | `FILE` | 💾 | "{filename} (+{add}, -{del})" |
| `GIT_COMMIT` | `GIT` | 📦 | "Committed {hash}" |
| `VALIDATION_END` | `VALIDATION` | ✓/✗ | "Valid" or "{n} errors" |
| `TEST_END` | `TEST` | ✓/✗ | "{passed}/{total} passed" |
| `CONSOLE_OUTPUT` | `OUTPUT` | │ | (last line preview) |
| `SYSTEM_TOKEN_UPDATE` | (bar update) | - | - |

### Implementation Phases

#### Phase 1: Timeline Widget Foundation

**Files to create:**
- `interpreter/terminal_interface/widgets/activity_timeline.py`
- `interpreter/terminal_interface/components/timeline_state.py`

**ActivityTimelineWidget** (extends `Tree`):
```python
class ActivityTimelineWidget(Tree[str]):
    def __init__(self, ui_state: UIState):
        super().__init__("⏱ Timeline", data="root")
        self._state = TimelineState()
        self._event_bus = get_event_bus()
        self._node_widgets: dict[str, TreeNode] = {}

    def on_mount(self) -> None:
        # Subscribe to relevant events
        for event_type in [AGENT_SPAWN, AGENT_OUTPUT, ...]:
            self._event_bus.subscribe(event_type, self._on_event)

    def _on_event(self, event: UIEvent) -> None:
        self.app.call_from_thread(self._process_event, event)
```

**TimelineState** (manages node data):
```python
@dataclass
class TimelineState:
    nodes: list[TimelineNode] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    max_nodes: int = 500

    def add_node(self, node: TimelineNode) -> None:
        with self._lock:
            self.nodes.append(node)
            if len(self.nodes) > self.max_nodes:
                self.nodes.pop(0)  # Remove oldest
```

#### Phase 2: Code Sidebar Widget

**Files to create:**
- `interpreter/terminal_interface/widgets/code_sidebar.py`

**CodeSidebarWidget** (collapsible code panel):
```python
class CodeSidebarWidget(Static):
    is_visible: reactive[bool] = reactive(True)
    code_blocks: reactive[list] = reactive([])

    DEFAULT_CSS = """
    CodeSidebarWidget {
        dock: right;
        width: 40%;
        min-width: 30;
        max-width: 80;
    }
    CodeSidebarWidget.hidden { display: none; }
    """

    def compose(self) -> ComposeResult:
        yield Static("▼ Code", id="sidebar-header")
        yield ScrollableContainer(id="code-container")

    def add_code_block(self, code: str, language: str) -> str:
        """Add code block, return ID for timeline linking"""
        block_id = str(uuid.uuid4())[:8]
        block = CodeBlockWidget(code=code, language=language, id=block_id)
        self.query_one("#code-container").mount(block)
        return block_id
```

**CSS additions** in `interpreter.tcss`:
```css
#code-sidebar {
    dock: right;
    width: 40%;
    border-left: solid $primary;
}

#code-sidebar.hidden {
    display: none;
}

#sidebar-header {
    height: 1;
    background: $surface;
    padding: 0 1;
}
```

#### Phase 3: Resource Bar Widget

**Files to create:**
- `interpreter/terminal_interface/widgets/resource_bar.py`

**ResourceBarWidget** (bottom status bar):
```python
class ResourceBarWidget(Static):
    tokens: reactive[int] = reactive(0)
    token_limit: reactive[int] = reactive(128000)
    memory_mb: reactive[float] = reactive(0.0)
    elapsed_seconds: reactive[float] = reactive(0.0)

    DEFAULT_CSS = """
    ResourceBarWidget {
        dock: bottom;
        height: 1;
        background: $surface;
    }
    """

    def render(self) -> RenderResult:
        pct = (self.tokens / self.token_limit) * 100
        bar_fill = int(pct / 10)
        bar = "▮" * bar_fill + "░" * (10 - bar_fill)

        # Color based on threshold
        color = "green" if pct < 60 else "yellow" if pct < 85 else "red"

        return Text.assemble(
            ("Tokens: ", "dim"),
            (f"{self.tokens:,}", color),
            (f" {bar} ", color),
            (f"{pct:.0f}%", color),
            ("  │  ", "dim"),
            ("Memory: ", "dim"),
            (f"{self.memory_mb:.0f}MB", "cyan"),
            ("  │  ", "dim"),
            ("Time: ", "dim"),
            (f"{self.elapsed_seconds:.1f}s", "cyan"),
        )
```

#### Phase 4: Layout Integration

**Modify:** `interpreter/terminal_interface/textual_app.py`

```python
def compose(self) -> ComposeResult:
    yield Header()
    yield StatusBar(...)

    # New three-panel layout
    with Horizontal(id="main-container"):
        yield ActivityTimelineWidget(self.ui_state, id="timeline")
        yield CodeSidebarWidget(id="code-sidebar")

    yield ResourceBarWidget(id="resource-bar")
    yield InputArea(id="input-area")
    yield Footer()

BINDINGS = [
    ...
    Binding("alt+c", "toggle_code_sidebar", "Code", show=True),
]

def action_toggle_code_sidebar(self) -> None:
    sidebar = self.query_one("#code-sidebar", CodeSidebarWidget)
    sidebar.toggle_class("hidden")
```

#### Phase 5: Event Routing

**Modify:** `interpreter/terminal_interface/textual_app.py`

Route code chunks to sidebar instead of output panel:

```python
def _on_code_start(self, event: UIEvent) -> None:
    """Route CODE_START to sidebar"""
    language = event.data.get("language", "python")
    sidebar = self.query_one("#code-sidebar", CodeSidebarWidget)
    block_id = sidebar.add_code_block("", language)

    # Also add timeline node
    timeline = self.query_one("#timeline", ActivityTimelineWidget)
    timeline.add_node(
        node_type=NodeType.CODE_EXEC,
        primary_text=f"Running {language}",
        code_block_id=block_id
    )

def _on_code_chunk(self, event: UIEvent) -> None:
    """Stream code to sidebar"""
    code = event.data.get("content", "")
    sidebar = self.query_one("#code-sidebar", CodeSidebarWidget)
    sidebar.append_to_current_block(code)
```

#### Phase 6: Scroll & Navigation

**Add to ActivityTimelineWidget:**

```python
auto_scroll: reactive[bool] = reactive(True)

def on_key(self, event: events.Key) -> None:
    if event.key in ("up", "down", "pageup", "pagedown", "k", "j"):
        self.auto_scroll = False  # Pause auto-scroll
    elif event.key == "g":
        self.scroll_end()
        self.auto_scroll = True  # Resume

def _add_node_to_tree(self, node: TimelineNode) -> None:
    # ... add node ...
    if self.auto_scroll:
        self.call_after_refresh(self.scroll_end)
```

### Mode Behavior

| Mode | Timeline | Sidebar | Resource Bar |
|------|----------|---------|-------------|
| ZEN | Hidden | Hidden | Hidden |
| STANDARD | Visible (compact) | Hidden | Visible |
| POWER | Visible (full) | Visible | Visible |
| DEBUG | Visible (verbose) | Visible | Visible (detailed) |

### Keyboard Navigation

| Key | Action |
|-----|--------|
| `Alt+C` | Toggle code sidebar |
| `↑/↓` or `j/k` | Scroll timeline |
| `Enter` | Expand/collapse node |
| `g` | Go to bottom, resume auto-scroll |
| `Home` | Go to top |
| `Tab` | Cycle focus: Input → Timeline → Sidebar |

## Acceptance Criteria

### Functional Requirements
- [x] Timeline shows all agent spawn/complete events with elapsed time
- [x] Timeline shows code execution start/end with duration
- [x] Timeline shows file changes with diff summary
- [x] Timeline shows validation/test results
- [x] Code streams to right sidebar, not main area
- [x] Sidebar collapses with Alt+C
- [x] Resource bar shows token usage with progress bar
- [x] Resource bar shows memory and elapsed time
- [x] Auto-scroll pauses on user interaction, resumes with 'g'
- [x] Nested nodes (agent actions under agent spawn)

### Non-Functional Requirements
- [x] Render latency <50ms for 500 nodes
- [x] Memory <100MB for 500 nodes
- [x] No Live context conflicts (lazy initialization)
- [x] Thread-safe node updates
- [x] 30fps refresh throttling

### Quality Gates
- [x] All existing tests pass
- [x] New widget tests for Timeline, Sidebar, ResourceBar
- [ ] Manual test: rapid agent spawning doesn't freeze
- [ ] Manual test: Ctrl+C during execution works

## Dependencies

**Existing components to leverage:**
- `EventBus` (`ui_events.py`) - event subscription
- `UIState` (`ui_state.py`) - shared state
- `CodeBlockWidget` (`code_block.py`) - syntax highlighting
- `ActivityStream` icons (`activity_stream.py`) - node icons
- `AgentTreeWidget` (`agent_tree.py`) - Tree widget pattern

**No new external dependencies required.**

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Live context conflicts | Medium | High | Lazy init, test multi-block scenarios |
| Performance with 500+ nodes | Medium | Medium | Max node limit, oldest purged |
| Scroll position jumps | Low | Medium | Preserve scroll offset on layout change |
| Existing keybinding conflicts | Low | Low | Check against CLAUDE.md keybindings |

## Test Plan

```python
# tests/test_activity_timeline.py

def test_timeline_node_creation():
    """Timeline creates nodes from events"""
    timeline = ActivityTimelineWidget(UIState())
    event = UIEvent(type=EventType.AGENT_SPAWN, data={"role": "scout"})
    timeline._on_event(event)
    assert len(timeline._state.nodes) == 1
    assert timeline._state.nodes[0].primary_text == "Scout started"

def test_timeline_max_nodes():
    """Timeline purges old nodes when limit exceeded"""
    state = TimelineState(max_nodes=10)
    for i in range(15):
        state.add_node(TimelineNode(id=str(i), ...))
    assert len(state.nodes) == 10
    assert state.nodes[0].id == "5"  # First 5 removed

def test_code_sidebar_toggle():
    """Alt+C toggles sidebar visibility"""
    app = InterpreterApp(...)
    sidebar = app.query_one("#code-sidebar")
    assert not sidebar.has_class("hidden")
    app.action_toggle_code_sidebar()
    assert sidebar.has_class("hidden")

def test_resource_bar_updates():
    """Resource bar updates from token events"""
    bar = ResourceBarWidget()
    bar.tokens = 5000
    bar.token_limit = 128000
    rendered = bar.render()
    assert "5,000" in rendered.plain
    assert "4%" in rendered.plain
```

## File Changes Summary

| Action | File | Description |
|--------|------|-------------|
| Create | `widgets/activity_timeline.py` | Timeline widget (~300 lines) |
| Create | `components/timeline_state.py` | Node state management (~100 lines) |
| Create | `widgets/code_sidebar.py` | Collapsible code panel (~150 lines) |
| Create | `widgets/resource_bar.py` | Bottom resource bar (~80 lines) |
| Modify | `textual_app.py` | Layout composition, event routing |
| Modify | `interpreter.tcss` | New widget styles |
| Modify | `ui_events.py` | Add timeline-specific events if needed |
| Create | `tests/test_activity_timeline.py` | Widget tests |

## References

### Internal
- Brainstorm: `docs/brainstorms/2026-02-01-activity-timeline-ui-brainstorm.md`
- Existing widget patterns: `widgets/agent_tree.py:34-324`
- Event system: `components/ui_events.py:32-99`
- Activity icons: `components/activity_stream.py:48-69`

### Documentation
- Textual Tree widget: https://textual.textualize.io/widgets/tree/
- Rich Text styling: https://rich.readthedocs.io/en/latest/text.html
