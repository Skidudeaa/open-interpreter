"""
BaseAgent - Abstract base class for specialized agents.

All agents share:
- Access to the OpenInterpreter instance
- Access to the SemanticEditGraph for memory
- A specialized system message for their role
- Result formatting
- Optional plugin support via PluginRegistry

Agents can be used standalone or orchestrated together.
"""

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Optional

# Import unified types (single source of truth)
from .types import AgentResult, AgentRole

if TYPE_CHECKING:
    from ...sdk.plugins import AgentPlugin, PluginRegistry
    from ..core import OpenInterpreter
    from ..memory import SemanticEditGraph

    # Forward reference for orchestrator
    from .orchestrator import AgentOrchestrator

# Lazy-loaded plugin registry to avoid circular imports
_PluginRegistry = None

# Global flag to prevent nested interpreter.chat() calls
# When True, agents will skip LLM calls and use fallbacks
_INTERPRETER_ACTIVE = False


def _get_plugin_registry_class():
    """Lazy load PluginRegistry to avoid circular imports."""
    global _PluginRegistry
    if _PluginRegistry is None:
        from ...sdk.plugins import PluginRegistry

        _PluginRegistry = PluginRegistry
    return _PluginRegistry


class BaseAgent(ABC):
    """
    Abstract base class for specialized agents.

    Subclasses must implement:
    - execute(): Main agent logic
    - get_system_message(): Role-specific system message

    Supports optional plugin system for extending behavior at hook points:
    - BEFORE_EXECUTE, AFTER_EXECUTE
    - BEFORE_LLM, AFTER_LLM
    - BEFORE_EDIT, AFTER_EDIT
    - ON_ERROR, ON_TOOL_CALL
    """

    # Class-level role definition
    role: AgentRole = AgentRole.SCOUT

    def __init__(
        self,
        interpreter: "OpenInterpreter",
        memory: Optional["SemanticEditGraph"] = None,
        plugins: list["AgentPlugin"] | None = None,
        name: str | None = None,
        orchestrator: Optional["AgentOrchestrator"] = None,
    ):
        """
        Initialize the agent.

        Args:
            interpreter: The OpenInterpreter instance to use
            memory: Optional shared SemanticEditGraph
            plugins: Optional list of AgentPlugin instances
            name: Optional agent name (defaults to role value)
            orchestrator: Optional orchestrator for inter-agent communication
        """
        self.interpreter = interpreter
        self._memory = memory
        self._name = name
        self._orchestrator: Optional[AgentOrchestrator] = orchestrator

        # Agent state
        self._active = False
        self._last_result: AgentResult | None = None
        self._current_context: str | None = None  # Track context for inter-agent calls
        self._call_depth: int = 0  # Prevent infinite agent loops
        self._in_llm_call: bool = False  # Prevent nested LLM calls

        # Initialize plugin registry
        self._plugin_registry: PluginRegistry | None = None
        if plugins:
            PluginRegistryClass = _get_plugin_registry_class()
            self._plugin_registry = PluginRegistryClass()
            for plugin in plugins:
                self._plugin_registry.register(plugin)

    @property
    def name(self) -> str:
        """Agent name for logging and plugin context."""
        return self._name or self.role.value

    @property
    def plugins(self) -> Optional["PluginRegistry"]:
        """Get the plugin registry."""
        return self._plugin_registry

    def register_plugin(self, plugin: "AgentPlugin") -> None:
        """
        Register a plugin with this agent.

        Args:
            plugin: Plugin to register
        """
        if self._plugin_registry is None:
            PluginRegistryClass = _get_plugin_registry_class()
            self._plugin_registry = PluginRegistryClass()
        self._plugin_registry.register(plugin)

    # =========================================================================
    # Inter-Agent Communication
    # =========================================================================

    MAX_AGENT_CALL_DEPTH = 3  # Prevent infinite agent loops

    def ask_agent(
        self,
        role: AgentRole,
        question: str,
        context: str | None = None,
    ) -> AgentResult:
        """
        Ask another agent a question and get a response.

        ARCHITECTURE: Enables agent collaboration by allowing any agent to
        query siblings for specialized knowledge.

        WHY: Scout can ask Architect about structure, Surgeon can ask Scout
        to find related files - agents can leverage each other's expertise.

        TRADEOFF: Adds latency (nested agent calls) but enables smarter behavior.
        Max depth prevents infinite loops.

        Args:
            role: The AgentRole to query
            question: The question to ask
            context: Optional additional context

        Returns:
            AgentResult from the queried agent

        Raises:
            RuntimeError: If orchestrator not set or max depth exceeded
        """
        if self._orchestrator is None:
            raise RuntimeError(
                f"{self.name} cannot call ask_agent(): no orchestrator set. "
                "Agents must be created via AgentOrchestrator for collaboration."
            )

        if self._call_depth >= self.MAX_AGENT_CALL_DEPTH:
            return AgentResult(
                role=role,
                success=False,
                error=f"Max agent call depth ({self.MAX_AGENT_CALL_DEPTH}) exceeded. "
                f"Preventing potential infinite loop.",
            )

        # Get the sibling agent
        sibling = self.get_sibling_agent(role)

        # Inherit call depth to prevent deep recursion
        sibling._call_depth = self._call_depth + 1

        # Combine contexts
        combined_context = context
        if self._current_context:
            if combined_context:
                combined_context = f"{self._current_context}\n\n{combined_context}"
            else:
                combined_context = self._current_context

        self.log(f"Asking {role.value}: {question[:50]}...")

        try:
            result = sibling.execute(question, context=combined_context)
            return result
        finally:
            # Reset sibling's call depth
            sibling._call_depth = 0

    def get_sibling_agent(self, role: AgentRole) -> "BaseAgent":
        """
        Get another agent from the orchestrator.

        Args:
            role: The AgentRole to get

        Returns:
            The agent instance

        Raises:
            RuntimeError: If orchestrator not set
        """
        if self._orchestrator is None:
            raise RuntimeError(
                f"{self.name} cannot get sibling agent: no orchestrator set."
            )
        return self._orchestrator.get_agent(role)

    def can_collaborate(self) -> bool:
        """Check if this agent can collaborate with others."""
        return self._orchestrator is not None

    def _run_hook_sync(self, hook_name: str, value: Any, **kwargs) -> Any:
        """
        Run a plugin hook synchronously.

        Wraps the async plugin hook in an event loop for synchronous agents.

        Args:
            hook_name: Name of the hook (e.g., 'before_execute')
            value: Value to transform
            **kwargs: Additional arguments

        Returns:
            Transformed value after all plugins
        """
        if self._plugin_registry is None:
            return value

        try:
            from ...sdk.plugins import HookPoint

            hook = HookPoint(hook_name)
            plugins = self._plugin_registry.get_plugins_for_hook(hook)

            if not plugins:
                return value

            # Run async hooks in sync context
            async def run_hooks():
                result = value
                for plugin in plugins:
                    method = getattr(plugin, f"on_{hook_name}", None)
                    if method:
                        try:
                            hook_result = await method(self, result, **kwargs)
                            if hook_result is not None:
                                result = hook_result
                        except Exception as e:
                            if self.interpreter.verbose:
                                print(f"[{self.name}] Plugin {plugin.name} error: {e}")
                return result

            # Use existing event loop or create new one
            try:
                asyncio.get_running_loop()
                # Already in async context - create task
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, run_hooks())
                    return future.result(timeout=30)
            except RuntimeError:
                # No running loop - safe to use asyncio.run
                return asyncio.run(run_hooks())

        except Exception as e:
            if self.interpreter.verbose:
                print(f"[{self.name}] Hook {hook_name} error: {e}")
            return value

    @property
    def memory(self) -> Optional["SemanticEditGraph"]:
        """Get the semantic memory (from interpreter if not set)."""
        if self._memory is not None:
            return self._memory
        return self.interpreter.semantic_graph

    @abstractmethod
    def execute(self, task: str, context: str | None = None) -> AgentResult:
        """
        Execute the agent's task.

        Args:
            task: The task description
            context: Optional context from previous agents

        Returns:
            AgentResult with the execution results
        """
        pass

    def run(self, task: str, context: str | None = None) -> AgentResult:
        """
        Run the agent with plugin hooks.

        This is the preferred entry point - wraps execute() with
        BEFORE_EXECUTE and AFTER_EXECUTE hooks.

        Args:
            task: The task description
            context: Optional context from previous agents

        Returns:
            AgentResult with the execution results
        """
        import time

        start_time = time.time()

        # Store context for inter-agent calls
        self._current_context = context

        # Run BEFORE_EXECUTE hook
        task = self._run_hook_sync("before_execute", task)

        try:
            self._active = True
            result = self.execute(task, context)

            # Run AFTER_EXECUTE hook
            result = self._run_hook_sync("after_execute", result)

            self._last_result = result
            return result

        except Exception as e:
            # Run ON_ERROR hook
            error_result = self._run_hook_sync("error", e)

            # If hook returns a string, it's a recovery message
            if isinstance(error_result, str):
                return AgentResult(
                    success=False,
                    role=self.role,
                    error=error_result,
                    execution_time=time.time() - start_time,
                )

            # Re-raise if not handled
            raise

        finally:
            self._active = False
            self._current_context = None  # Clear context after execution

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
        context: str | None = None,
        additional_context: str | None = None,
    ) -> list[dict[str, str]]:
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

        messages.append(
            {
                "role": "user",
                "type": "message",
                "content": "\n".join(user_content_parts),
            }
        )

        return messages

    def run_interpreter(
        self,
        messages: list[dict[str, str]],
        system_message: str | None = None,
        timeout: float = 30.0,
    ) -> str:
        """
        Run the interpreter with the given messages.

        Args:
            messages: Messages to send
            system_message: Optional override system message
            timeout: Timeout in seconds (default 30s)

        Returns:
            The assistant's response content
        """
        # Prevent nested LLM calls - can cause hangs
        global _INTERPRETER_ACTIVE
        if self._in_llm_call or _INTERPRETER_ACTIVE:
            self.log("Skipping LLM call to prevent nested interpreter hang")
            return ""

        # Store original settings
        original_system = self.interpreter.system_message
        original_auto_run = self.interpreter.auto_run
        original_loop = self.interpreter.loop

        self._in_llm_call = True
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

            # Collect response with timeout protection
            import time

            response_parts = []
            start_time = time.time()

            # Pass empty message to trigger response to pre-populated messages
            for chunk in self.interpreter.chat(message="", display=False, stream=True):
                if time.time() - start_time > timeout:
                    self.log(f"LLM call timed out after {timeout}s")
                    break
                if chunk.get("type") == "message" and chunk.get("role") == "assistant":
                    response_parts.append(chunk.get("content", ""))

            return "".join(response_parts)

        finally:
            self._in_llm_call = False
            # Restore original settings
            self.interpreter.system_message = original_system
            self.interpreter.auto_run = original_auto_run
            self.interpreter.loop = original_loop

    def get_memory_context(self, file_path: str | None = None) -> str:
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
    role: AgentRole, success: bool, content: Any, **kwargs
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
    return AgentResult(role=role, success=success, content=content, **kwargs)
