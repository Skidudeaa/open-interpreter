"""
Unified Agent Types

Single source of truth for agent-related types used by both
core agents (interpreter/core/agents/) and SDK (interpreter/sdk/).

This eliminates duplication and ensures type consistency across
the agent system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AgentRole(Enum):
    """
    Roles for specialized agents.

    Maps to specific agent implementations and determines
    default behavior and capabilities.
    """

    SCOUT = "scout"  # Exploration and context gathering
    ARCHITECT = "architect"  # Structure and design analysis
    SURGEON = "surgeon"  # Precise code editing
    VALIDATOR = "validator"  # Testing and validation
    HISTORIAN = "historian"  # Memory and documentation
    REVIEWER = "reviewer"  # Code review
    TESTER = "tester"  # Test generation
    CUSTOM = "custom"  # User-defined agents


class AgentStatus(Enum):
    """Agent lifecycle states."""

    PENDING = "pending"  # Created but not started
    RUNNING = "running"  # Actively processing
    COMPLETE = "complete"  # Finished successfully (note: not COMPLETED)
    ERROR = "error"  # Finished with error
    CANCELLED = "cancelled"  # User cancelled

    # Backward compatibility alias
    COMPLETED = "complete"


@dataclass
class AgentConfig:
    """
    Configuration for an agent.

    Defines all settings needed to create and run an agent instance.
    Used by AgentBuilder and AgentOrchestrator.
    """

    name: str
    system_prompt: str = ""
    role: AgentRole = AgentRole.CUSTOM
    tools: list[str] = field(default_factory=list)
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int = 300  # seconds
    memory_enabled: bool = True
    memory_path: str | None = None
    context_window: int = 128000
    auto_run: bool = True
    safe_mode: str = "auto"


@dataclass
class AgentResult:
    """
    Result from an agent's execution.

    Unified result type that works for both core agents and SDK agents.
    Includes both simple fields (success, output) and structured data
    (files_found, edits_proposed, etc.).
    """

    # Core fields (always present)
    success: bool
    role: AgentRole = AgentRole.CUSTOM

    # Output fields
    output: str = ""
    content: Any = None  # Role-specific structured content
    error: str | None = None

    # Messages from agent conversation
    messages: list[dict[str, Any]] = field(default_factory=list)

    # Metrics
    execution_time: float = 0.0  # seconds
    duration_ms: float | None = None  # milliseconds (computed if not set)
    tokens_used: int = 0
    timestamp: datetime = field(default_factory=datetime.now)

    # Structured findings (optional, role-dependent)
    files_found: list[str] = field(default_factory=list)
    symbols_found: list[str] = field(default_factory=list)
    edits_proposed: list[dict] = field(default_factory=list)
    tests_run: list[dict] = field(default_factory=list)

    # For chaining agents
    context_for_next: str | None = None
    suggestions: list[str] = field(default_factory=list)

    # Extension point
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Compute duration_ms from execution_time if not set."""
        if self.duration_ms is None and self.execution_time:
            self.duration_ms = self.execution_time * 1000

    def to_context_string(self) -> str:
        """Convert to a string for passing to next agent."""
        if self.context_for_next:
            return self.context_for_next

        parts = [f"## {self.role.value.title()} Agent Result"]

        # Add output or content
        if self.output:
            parts.append(self.output)
        elif isinstance(self.content, str):
            parts.append(self.content)
        elif isinstance(self.content, list):
            for item in self.content[:20]:
                parts.append(f"- {item}")
        elif isinstance(self.content, dict):
            for key, value in list(self.content.items())[:20]:
                parts.append(f"- {key}: {value}")

        if self.files_found:
            parts.append(f"\nFiles: {', '.join(self.files_found[:10])}")

        if self.symbols_found:
            parts.append(f"\nSymbols: {', '.join(self.symbols_found[:10])}")

        if self.error:
            parts.append(f"\nError: {self.error}")

        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "role": self.role.value,
            "output": self.output,
            "error": self.error,
            "execution_time": self.execution_time,
            "tokens_used": self.tokens_used,
            "files_found": self.files_found,
            "symbols_found": self.symbols_found,
            "metadata": self.metadata,
        }


@dataclass
class WorkflowStep:
    """
    A step in a multi-agent workflow.

    Used by AgentOrchestrator to define execution order
    and dependencies between agents.
    """

    agent_role: AgentRole
    task: str
    depends_on: list[str] = field(default_factory=list)  # IDs of prerequisite steps
    config_overrides: dict[str, Any] = field(default_factory=dict)
    step_id: str = ""

    def __post_init__(self):
        """Generate step_id if not provided."""
        if not self.step_id:
            import uuid

            self.step_id = str(uuid.uuid4())[:8]


@dataclass
class WorkflowResult:
    """Result of a multi-agent workflow execution."""

    success: bool
    steps_completed: int
    steps_total: int
    results: dict[str, AgentResult] = field(default_factory=dict)  # step_id -> result
    errors: list[str] = field(default_factory=list)
    total_duration_ms: float = 0.0
    total_tokens: int = 0

    @property
    def all_files_found(self) -> list[str]:
        """Aggregate all files found across steps."""
        files = []
        for result in self.results.values():
            files.extend(result.files_found)
        return list(set(files))

    @property
    def all_edits_proposed(self) -> list[dict]:
        """Aggregate all edits proposed across steps."""
        edits = []
        for result in self.results.values():
            edits.extend(result.edits_proposed)
        return edits
