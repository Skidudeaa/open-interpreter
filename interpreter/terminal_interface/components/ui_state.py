"""
Centralized UI State Management

Single source of truth for all terminal UI state. Replaces ad-hoc
function-scoped variables with a proper state container.

Part of Phase 0: Foundation (must be implemented before other UI phases)
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Optional, Set, Deque
from collections import deque
import time


class UIMode(Enum):
    """UI complexity modes - auto-escalates based on activity"""
    ZEN = auto()       # Minimal: conversation only
    STANDARD = auto()  # + Status bar, collapsible outputs
    POWER = auto()     # + Context panel, agent strip, metrics
    DEBUG = auto()     # + Token counts, timing, raw chunks


class AgentStatus(Enum):
    """Agent lifecycle states"""
    PENDING = auto()   # Created but not started
    RUNNING = auto()   # Actively processing
    COMPLETE = auto()  # Finished successfully
    ERROR = auto()     # Finished with error
    CANCELLED = auto() # User cancelled


class AgentRole(Enum):
    """Agent specialization roles (maps to interpreter/core/agents/)"""
    SCOUT = "scout"         # Fast codebase exploration
    SURGEON = "surgeon"     # Precise code editing
    ARCHITECT = "architect" # Structural analysis
    VALIDATOR = "validator" # Testing & verification
    HISTORIAN = "historian" # Memory & documentation
    CUSTOM = "custom"       # User-defined agents


@dataclass
class AgentState:
    """
    State of a single agent instance.

    Used by the AgentStrip component to display real-time status.
    """
    id: str
    role: AgentRole
    status: AgentStatus = AgentStatus.PENDING
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    last_lines: Deque[str] = field(default_factory=lambda: deque(maxlen=5))
    error_summary: Optional[str] = None
    parent_id: Optional[str] = None  # For hierarchical agent trees

    @property
    def elapsed_seconds(self) -> float:
        """Time since agent started"""
        end = self.completed_at or time.time()
        return end - self.started_at

    @property
    def elapsed_display(self) -> str:
        """Human-readable elapsed time"""
        secs = self.elapsed_seconds
        if secs < 60:
            return f"{secs:.1f}s"
        elif secs < 3600:
            return f"{secs/60:.1f}m"
        else:
            return f"{secs/3600:.1f}h"

    @property
    def status_icon(self) -> str:
        """Status indicator for display"""
        return {
            AgentStatus.PENDING: "○",
            AgentStatus.RUNNING: "⏳",
            AgentStatus.COMPLETE: "✓",
            AgentStatus.ERROR: "✗",
            AgentStatus.CANCELLED: "⊘",
        }.get(self.status, "?")


@dataclass
class ConversationState:
    """State for the conversation display"""
    message_count: int = 0
    current_block_index: int = 0  # For navigation (j/k keys)
    scroll_offset: int = 0        # Viewport scroll position


@dataclass
class ContextState:
    """State for the context panel (Phase 3)"""
    variables: Dict[str, str] = field(default_factory=dict)  # name -> type/preview
    functions: Dict[str, str] = field(default_factory=dict)  # name -> signature
    execution_time_ms: float = 0.0
    memory_mb: float = 0.0


@dataclass
class UIState:
    """
    Master state container for the terminal UI.

    Single source of truth that all UI components read from.
    Updated exclusively through the EventBus.

    Example:
        state = UIState()
        state.mode = UIMode.POWER
        state.active_agents["agent-1"] = AgentState(id="agent-1", role=AgentRole.SCOUT)
    """

    # Display mode (auto-escalates, manual override)
    mode: UIMode = UIMode.ZEN

    # Agent tracking
    active_agents: Dict[str, AgentState] = field(default_factory=dict)
    selected_agent_id: Optional[str] = None

    # Panel visibility (Alt+H toggles, mode-dependent)
    panels_visible: Set[str] = field(default_factory=set)  # "context", "agents", etc.

    # Conversation state
    conversation: ConversationState = field(default_factory=ConversationState)

    # Context panel state (Phase 3)
    context: ContextState = field(default_factory=ContextState)

    # Token usage (for context window meter)
    context_tokens: int = 0
    context_limit: int = 128000  # Model-dependent

    # Streaming state
    is_streaming: bool = False
    is_responding: bool = False

    # UI mode scoring (for auto-escalation)
    complexity_score: int = 0

    # Error state
    last_error: Optional[str] = None

    @property
    def context_usage_percent(self) -> float:
        """Percentage of context window used"""
        if self.context_limit == 0:
            return 0.0
        return (self.context_tokens / self.context_limit) * 100

    @property
    def has_active_agents(self) -> bool:
        """True if any agents are currently running"""
        return any(
            a.status == AgentStatus.RUNNING
            for a in self.active_agents.values()
        )

    @property
    def agent_strip_visible(self) -> bool:
        """Agent strip appears when agents exist (not just running)"""
        return len(self.active_agents) > 0

    @property
    def context_panel_visible(self) -> bool:
        """Context panel appears in POWER/DEBUG mode or when content exists"""
        if self.mode in (UIMode.POWER, UIMode.DEBUG):
            return True
        if "context" in self.panels_visible:
            return True
        # Auto-show if we have interesting content
        return (
            len(self.context.variables) > 0 or
            len(self.context.functions) > 0
        )

    def reset_agents(self) -> None:
        """Clear all agent state (e.g., on new conversation)"""
        self.active_agents.clear()
        self.selected_agent_id = None

    def add_agent(self, agent_id: str, role: AgentRole, parent_id: Optional[str] = None) -> AgentState:
        """Register a new agent and return its state"""
        agent = AgentState(id=agent_id, role=role, parent_id=parent_id)
        self.active_agents[agent_id] = agent
        # Auto-escalate complexity
        self.complexity_score += 10
        return agent

    def update_agent_status(self, agent_id: str, status: AgentStatus, error: Optional[str] = None) -> None:
        """Update an agent's status"""
        if agent_id in self.active_agents:
            agent = self.active_agents[agent_id]
            agent.status = status
            if status in (AgentStatus.COMPLETE, AgentStatus.ERROR, AgentStatus.CANCELLED):
                agent.completed_at = time.time()
            if error:
                agent.error_summary = error

    def append_agent_output(self, agent_id: str, line: str) -> None:
        """Add a line to an agent's output preview"""
        if agent_id in self.active_agents:
            self.active_agents[agent_id].last_lines.append(line)
