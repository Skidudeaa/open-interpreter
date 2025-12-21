"""
BaseAgent - Abstract base class for specialized agents.

All agents share:
- Access to the OpenInterpreter instance
- Access to the SemanticEditGraph for memory
- A specialized system message for their role
- Result formatting

Agents can be used standalone or orchestrated together.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..core import OpenInterpreter
    from ..memory import SemanticEditGraph


class AgentRole(Enum):
    """Roles for specialized agents."""
    SCOUT = "scout"           # Exploration and context gathering
    ARCHITECT = "architect"   # Structure and design analysis
    SURGEON = "surgeon"       # Precise code editing
    VALIDATOR = "validator"   # Testing and validation
    HISTORIAN = "historian"   # Memory and documentation


@dataclass
class AgentResult:
    """
    Result from an agent's execution.
    """
    role: AgentRole
    success: bool
    content: Any  # Role-specific content

    # Metadata
    duration_ms: Optional[float] = None
    tokens_used: int = 0
    timestamp: datetime = field(default_factory=datetime.now)

    # Optional structured data
    files_found: List[str] = field(default_factory=list)
    symbols_found: List[str] = field(default_factory=list)
    edits_proposed: List[Dict] = field(default_factory=list)
    tests_run: List[Dict] = field(default_factory=list)

    # For chaining
    context_for_next: Optional[str] = None
    suggestions: List[str] = field(default_factory=list)

    def to_context_string(self) -> str:
        """Convert to a string for passing to next agent."""
        if self.context_for_next:
            return self.context_for_next

        parts = [f"## {self.role.value.title()} Agent Result"]

        if isinstance(self.content, str):
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

        return "\n".join(parts)


class BaseAgent(ABC):
    """
    Abstract base class for specialized agents.

    Subclasses must implement:
    - execute(): Main agent logic
    - get_system_message(): Role-specific system message
    """

    # Class-level role definition
    role: AgentRole = AgentRole.SCOUT

    def __init__(
        self,
        interpreter: "OpenInterpreter",
        memory: Optional["SemanticEditGraph"] = None,
    ):
        """
        Initialize the agent.

        Args:
            interpreter: The OpenInterpreter instance to use
            memory: Optional shared SemanticEditGraph
        """
        self.interpreter = interpreter
        self._memory = memory

        # Agent state
        self._active = False
        self._last_result: Optional[AgentResult] = None

    @property
    def memory(self) -> Optional["SemanticEditGraph"]:
        """Get the semantic memory (from interpreter if not set)."""
        if self._memory is not None:
            return self._memory
        return self.interpreter.semantic_graph

    @abstractmethod
    def execute(self, task: str, context: Optional[str] = None) -> AgentResult:
        """
        Execute the agent's task.

        Args:
            task: The task description
            context: Optional context from previous agents

        Returns:
            AgentResult with the execution results
        """
        pass

    @abstractmethod
    def get_system_message(self) -> str:
        """
        Get the role-specific system message.

        Returns:
            System message string for this agent role
        """
        pass

    def prepare_messages(
        self,
        task: str,
        context: Optional[str] = None,
        additional_context: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """
        Prepare messages for the LLM call.

        Args:
            task: The main task
            context: Context from previous agents
            additional_context: Any additional context

        Returns:
            List of message dictionaries
        """
        messages = []

        # Add context if provided
        user_content_parts = []

        if context:
            user_content_parts.append(f"## Previous Context\n{context}\n")

        if additional_context:
            user_content_parts.append(f"## Additional Context\n{additional_context}\n")

        user_content_parts.append(f"## Task\n{task}")

        messages.append({
            "role": "user",
            "type": "message",
            "content": "\n".join(user_content_parts)
        })

        return messages

    def run_interpreter(
        self,
        messages: List[Dict[str, str]],
        system_message: Optional[str] = None,
    ) -> str:
        """
        Run the interpreter with the given messages.

        Args:
            messages: Messages to send
            system_message: Optional override system message

        Returns:
            The assistant's response content
        """
        # Store original settings
        original_system = self.interpreter.system_message
        original_auto_run = self.interpreter.auto_run
        original_loop = self.interpreter.loop

        try:
            # Apply agent settings
            if system_message:
                self.interpreter.system_message = system_message
            else:
                self.interpreter.system_message = self.get_system_message()

            # Agents typically run without user confirmation
            self.interpreter.auto_run = True
            self.interpreter.loop = False

            # Set messages and run
            self.interpreter.messages = messages.copy()

            # Collect response
            response_parts = []
            for chunk in self.interpreter.chat(display=False, stream=True):
                if chunk.get("type") == "message" and chunk.get("role") == "assistant":
                    response_parts.append(chunk.get("content", ""))

            return "".join(response_parts)

        finally:
            # Restore original settings
            self.interpreter.system_message = original_system
            self.interpreter.auto_run = original_auto_run
            self.interpreter.loop = original_loop

    def get_memory_context(self, file_path: Optional[str] = None) -> str:
        """
        Get relevant context from semantic memory.

        Args:
            file_path: Optional file to get history for

        Returns:
            Memory context string
        """
        if not self.memory:
            return ""

        if file_path:
            return self.memory.get_institutional_knowledge(file_path)

        return ""

    def log(self, message: str):
        """Log a message (for debugging)."""
        if self.interpreter.verbose:
            print(f"[{self.role.value}] {message}")


# Utility functions for agents

def create_result(
    role: AgentRole,
    success: bool,
    content: Any,
    **kwargs
) -> AgentResult:
    """
    Convenience function to create an AgentResult.

    Args:
        role: The agent role
        success: Whether execution succeeded
        content: The result content
        **kwargs: Additional result fields

    Returns:
        AgentResult instance
    """
    return AgentResult(
        role=role,
        success=success,
        content=content,
        **kwargs
    )
