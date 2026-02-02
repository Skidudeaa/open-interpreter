"""
Centralized UI State Management

Single source of truth for all terminal UI state. Replaces ad-hoc
function-scoped variables with a proper state container.

Part of Phase 0: Foundation (must be implemented before other UI phases)
"""

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto

# Import unified agent types from core (single source of truth)
# Use lazy import to avoid circular dependencies
_AgentRole = None
_AgentStatus = None


def _get_agent_types():
    """Lazy load agent types to avoid circular imports."""
    global _AgentRole, _AgentStatus
    if _AgentRole is None:
        from ...core.agents.types import AgentRole, AgentStatus

        _AgentRole = AgentRole
        _AgentStatus = AgentStatus
    return _AgentRole, _AgentStatus


class UIMode(Enum):
    """UI complexity modes - auto-escalates based on activity"""

    ZEN = auto()  # Minimal: conversation only
    STANDARD = auto()  # + Status bar, collapsible outputs
    POWER = auto()  # + Context panel, agent strip, metrics
    DEBUG = auto()  # + Token counts, timing, raw chunks


# UI-specific status enum that maps to core AgentStatus
# Using auto() for simpler comparison in UI code
class AgentStatus(Enum):
    """Agent lifecycle states (UI version)"""

    PENDING = auto()  # Created but not started
    RUNNING = auto()  # Actively processing
    COMPLETE = auto()  # Finished successfully
    ERROR = auto()  # Finished with error
    CANCELLED = auto()  # User cancelled

    @classmethod
    def from_core_status(cls, core_status):
        """Convert from core.agents.types.AgentStatus to UI AgentStatus."""
        if core_status is None:
            return cls.PENDING
        mapping = {
            "pending": cls.PENDING,
            "running": cls.RUNNING,
            "complete": cls.COMPLETE,
            "error": cls.ERROR,
            "cancelled": cls.CANCELLED,
        }
        return mapping.get(core_status.value, cls.PENDING)


class AgentRole(Enum):
    """Agent specialization roles (maps to interpreter/core/agents/)"""

    SCOUT = "scout"  # Fast codebase exploration
    SURGEON = "surgeon"  # Precise code editing
    ARCHITECT = "architect"  # Structural analysis
    VALIDATOR = "validator"  # Testing & verification
    HISTORIAN = "historian"  # Memory & documentation
    REVIEWER = "reviewer"  # Code review
    TESTER = "tester"  # Test generation
    CUSTOM = "custom"  # User-defined agents

    @classmethod
    def from_core_role(cls, core_role):
        """
        Convert from core.agents.types.AgentRole to UI AgentRole.

        Args:
            core_role: AgentRole from core.agents.types

        Returns:
            UI AgentRole instance
        """
        # Map by value string
        role_value = core_role.value if hasattr(core_role, "value") else str(core_role)
        for ui_role in cls:
            if ui_role.value == role_value:
                return ui_role
        return cls.CUSTOM


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
    completed_at: float | None = None
    last_lines: deque[str] = field(default_factory=lambda: deque(maxlen=5))
    error_summary: str | None = None
    parent_id: str | None = None  # For hierarchical agent trees

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
            return f"{secs / 60:.1f}m"
        else:
            return f"{secs / 3600:.1f}h"

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
    scroll_offset: int = 0  # Viewport scroll position


@dataclass
class ContextState:
    """State for the context panel (Phase 3)"""

    variables: dict[str, str] = field(default_factory=dict)  # name -> type/preview
    functions: dict[str, str] = field(default_factory=dict)  # name -> signature
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
    active_agents: dict[str, AgentState] = field(default_factory=dict)
    selected_agent_id: str | None = None

    # Panel visibility (Alt+H toggles, mode-dependent)
    panels_visible: set[str] = field(default_factory=set)  # "context", "agents", etc.

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
    last_error: str | None = None

    # Thread safety lock for mutable state
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def context_usage_percent(self) -> float:
        """Percentage of context window used (thread-safe)"""
        with self._lock:
            if self.context_limit == 0:
                return 0.0
            return (self.context_tokens / self.context_limit) * 100

    @property
    def has_active_agents(self) -> bool:
        """True if any agents are currently running"""
        with self._lock:
            return any(
                a.status == AgentStatus.RUNNING for a in self.active_agents.values()
            )

    @property
    def agent_strip_visible(self) -> bool:
        """Agent strip appears when agents exist (not just running)"""
        with self._lock:
            return len(self.active_agents) > 0

    @property
    def context_panel_visible(self) -> bool:
        """Context panel appears in POWER/DEBUG mode or when content exists (thread-safe)"""
        with self._lock:
            if self.mode in (UIMode.POWER, UIMode.DEBUG):
                return True
            if "context" in self.panels_visible:
                return True
            # Auto-show if we have interesting content
            return len(self.context.variables) > 0 or len(self.context.functions) > 0

    def reset_agents(self) -> None:
        """Clear all agent state (e.g., on new conversation)"""
        with self._lock:
            self.active_agents.clear()
            self.selected_agent_id = None

    def add_agent(
        self, agent_id: str, role: AgentRole, parent_id: str | None = None
    ) -> AgentState:
        """Register a new agent and return its state"""
        agent = AgentState(id=agent_id, role=role, parent_id=parent_id)
        with self._lock:
            self.active_agents[agent_id] = agent
            # Auto-escalate complexity
            self.complexity_score += 10
        return agent

    def update_agent_status(
        self, agent_id: str, status: AgentStatus, error: str | None = None
    ) -> None:
        """Update an agent's status"""
        with self._lock:
            if agent_id in self.active_agents:
                agent = self.active_agents[agent_id]
                agent.status = status
                if status in (
                    AgentStatus.COMPLETE,
                    AgentStatus.ERROR,
                    AgentStatus.CANCELLED,
                ):
                    agent.completed_at = time.time()
                if error:
                    agent.error_summary = error

    def append_agent_output(self, agent_id: str, line: str) -> None:
        """Add a line to an agent's output preview"""
        with self._lock:
            if agent_id in self.active_agents:
                self.active_agents[agent_id].last_lines.append(line)

    def auto_purge_agents(self, max_age_seconds: float = 300.0) -> int:
        """
        Remove completed agents older than max_age_seconds.

        Prevents unbounded growth of active_agents dict in long sessions.
        Called periodically or on cleanup.

        Args:
            max_age_seconds: Maximum age for completed agents (default 5 minutes)

        Returns:
            Number of agents purged
        """
        now = time.time()
        with self._lock:
            to_remove = [
                agent_id
                for agent_id, agent in self.active_agents.items()
                if agent.completed_at is not None
                and (now - agent.completed_at) > max_age_seconds
            ]
            for agent_id in to_remove:
                del self.active_agents[agent_id]
            # Clear selection if selected agent was purged
            if self.selected_agent_id in to_remove:
                self.selected_agent_id = None
            return len(to_remove)

    def set_context_tokens(self, tokens: int, limit: int | None = None) -> None:
        """Thread-safe setter for context token tracking"""
        with self._lock:
            self.context_tokens = tokens
            if limit is not None:
                self.context_limit = limit

    def set_streaming(self, is_streaming: bool) -> None:
        """Thread-safe setter for streaming state"""
        with self._lock:
            self.is_streaming = is_streaming

    def set_responding(self, is_responding: bool) -> None:
        """Thread-safe setter for responding state"""
        with self._lock:
            self.is_responding = is_responding
