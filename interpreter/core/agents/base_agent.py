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
import inspect
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
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

# Global flag (legacy). Kept for compatibility in case other modules import it.
# Do NOT gate agent LLM calls on this: agent routing may happen while interpreter is already "active".
_INTERPRETER_ACTIVE = False

# Serialize interpreter.chat usage across agents/threads.
# WHY: OpenInterpreter is typically not thread-safe; concurrent calls corrupt shared state.
# Also prevents nested agent->agent LLM calls in the same thread.
_LLM_CALL_LOCK = threading.Lock()
_LLM_THREAD_STATE = threading.local()

# Reuse a single executor for running async plugin hooks from sync contexts when an event loop is running.
_HOOK_EXECUTOR: ThreadPoolExecutor | None = None


def _get_plugin_registry_class():
    """Lazy load PluginRegistry to avoid circular imports."""
    global _PluginRegistry
    if _PluginRegistry is None:
        from ...sdk.plugins import PluginRegistry

        _PluginRegistry = PluginRegistry
    return _PluginRegistry


def _get_hook_executor() -> ThreadPoolExecutor:
    """Create/reuse a thread pool for plugin hook execution."""
    global _HOOK_EXECUTOR
    if _HOOK_EXECUTOR is None:
        _HOOK_EXECUTOR = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="agent-hooks",
        )
    return _HOOK_EXECUTOR


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

    # Prevent infinite agent loops in ask_agent()
    MAX_AGENT_CALL_DEPTH = 3

    # Default timeout for plugin hook execution
    PLUGIN_HOOK_TIMEOUT_S = 30.0

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
        self._current_context: str | None = None  # for inter-agent calls
        self._call_depth: int = 0  # prevent infinite agent loops
        self._in_llm_call: bool = False  # prevent nested LLM calls per-agent

        # Initialize plugin registry
        self._plugin_registry: Optional[PluginRegistry] = None
        if plugins:
            PluginRegistryClass = _get_plugin_registry_class()
            self._plugin_registry = PluginRegistryClass()
            for plugin in plugins:
                self._plugin_registry.register(plugin)

    # =========================================================================
    # Introspection / lifecycle
    # =========================================================================

    @property
    def name(self) -> str:
        """Agent name for logging and plugin context."""
        return self._name or self.role.value

    @property
    def active(self) -> bool:
        """Whether this agent is currently executing."""
        return self._active

    @property
    def last_result(self) -> AgentResult | None:
        """Last AgentResult produced by this agent (if any)."""
        return self._last_result

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

    @property
    def memory(self) -> Optional["SemanticEditGraph"]:
        """
        Get the semantic memory.

        WHY: Most agents expect shared memory even if not explicitly passed in.
        """
        if self._memory is not None:
            return self._memory
        return getattr(self.interpreter, "semantic_graph", None)

    # =========================================================================
    # Inter-Agent Communication
    # =========================================================================

    def can_collaborate(self) -> bool:
        """Check if this agent can collaborate with others."""
        return self._orchestrator is not None

    def get_sibling_agent(self, role: AgentRole) -> "BaseAgent":
        """
        Get another agent from the orchestrator.

        Args:
            role: The AgentRole to get

        Returns:
            The agent instance
        """
        if self._orchestrator is None:
            raise RuntimeError(
                f"{self.name} cannot get sibling agent: no orchestrator set."
            )
        return self._orchestrator.get_agent(role)

    def ask_agent(
        self,
        role: AgentRole,
        question: str,
        context: str | None = None,
    ) -> AgentResult:
        """
        Ask another agent a question and get a response.

        WHY: Enables agent collaboration (Scout <-> Architect <-> Surgeon, etc.).
        SAFETY: Bounded depth prevents accidental recursion loops.

        Args:
            role: The AgentRole to query
            question: The question to ask
            context: Optional additional context

        Returns:
            AgentResult from the queried agent
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
                error=(
                    f"Max agent call depth ({self.MAX_AGENT_CALL_DEPTH}) exceeded. "
                    "Preventing potential infinite loop."
                ),
            )

        sibling = self.get_sibling_agent(role)

        # Combine contexts (caller context first, then provided context)
        combined_context = context
        if self._current_context:
            combined_context = (
                f"{self._current_context}\n\n{combined_context}"
                if combined_context
                else self._current_context
            )

        # Preserve sibling state (avoid stomping when reused)
        prev_depth = sibling._call_depth
        prev_context = sibling._current_context

        sibling._call_depth = self._call_depth + 1
        sibling._current_context = combined_context

        self.log(
            f"Asking {role.value}: {question[:50]}{'...' if len(question) > 50 else ''}"
        )

        try:
            return sibling.execute(question, context=combined_context)
        finally:
            sibling._call_depth = prev_depth
            sibling._current_context = prev_context

    # =========================================================================
    # Plugin hooks
    # =========================================================================

    def _resolve_hook(self, hook_name: str):
        """
        Resolve a hook name into the HookPoint enum (if available).

        WHY: Different versions may name hooks differently; we try a few variants.
        """
        from ...sdk.plugins import HookPoint

        # Common cases: exact value, enum name, upper variants.
        for candidate in (hook_name, hook_name.lower(), hook_name.upper()):
            try:
                return HookPoint(candidate)
            except Exception:
                pass

        # Try enum-name style lookup (e.g., BEFORE_EXECUTE).
        for candidate in (hook_name, hook_name.upper()):
            try:
                return HookPoint[candidate]
            except Exception:
                pass

        # Unknown hook: let caller treat as "no plugins".
        raise ValueError(f"Unknown HookPoint: {hook_name}")

    async def _run_hook_async(self, hook_name: str, value: Any, **kwargs) -> Any:
        """
        Run a plugin hook in async context.

        Args:
            hook_name: Name of the hook (e.g., 'before_execute')
            value: Value to transform
            **kwargs: Additional arguments passed to plugins

        Returns:
            Transformed value after all plugins.
        """
        if self._plugin_registry is None:
            return value

        try:
            hook = self._resolve_hook(hook_name)
        except Exception:
            # If hook can't be resolved, act like no plugins apply.
            return value

        plugins = self._plugin_registry.get_plugins_for_hook(hook)
        if not plugins:
            return value

        result = value
        for plugin in plugins:
            method = getattr(plugin, f"on_{hook_name}", None)
            if not method:
                continue
            try:
                maybe = method(self, result, **kwargs)
                hook_result = await maybe if inspect.isawaitable(maybe) else maybe
                if hook_result is not None:
                    result = hook_result
            except Exception as e:
                if getattr(self.interpreter, "verbose", False):
                    print(
                        f"[{self.name}] Plugin {getattr(plugin, 'name', plugin)} error: {e}"
                    )
        return result

    def _run_hook_sync(self, hook_name: str, value: Any, **kwargs) -> Any:
        """
        Run a plugin hook synchronously.

        Wraps async plugin hooks safely:
          - If no event loop is running: asyncio.run()
          - If an event loop is running (e.g., notebooks): run in a dedicated thread

        WHY: Agents are mostly synchronous; plugin authors still want async hooks.
        """
        if self._plugin_registry is None:
            return value

        async def runner():
            return await self._run_hook_async(hook_name, value, **kwargs)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop in this thread.
            try:
                return asyncio.run(runner())
            except Exception as e:
                if getattr(self.interpreter, "verbose", False):
                    print(f"[{self.name}] Hook {hook_name} error: {e}")
                return value

        # Running loop exists: offload to a thread to avoid "asyncio.run() cannot be called..."
        executor = _get_hook_executor()
        try:
            fut = executor.submit(lambda: asyncio.run(runner()))
            return fut.result(timeout=self.PLUGIN_HOOK_TIMEOUT_S)
        except FuturesTimeoutError:
            if getattr(self.interpreter, "verbose", False):
                print(
                    f"[{self.name}] Hook {hook_name} timed out after {self.PLUGIN_HOOK_TIMEOUT_S}s"
                )
            return value
        except Exception as e:
            if getattr(self.interpreter, "verbose", False):
                print(f"[{self.name}] Hook {hook_name} error: {e}")
            return value

    # =========================================================================
    # Core execution
    # =========================================================================

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
        raise NotImplementedError

    def run(self, task: str, context: str | None = None) -> AgentResult:
        """
        Run the agent with plugin hooks.

        Preferred entry point: wraps execute() with BEFORE_EXECUTE and AFTER_EXECUTE hooks.

        Args:
            task: The task description
            context: Optional context from previous agents

        Returns:
            AgentResult with the execution results
        """
        start = time.perf_counter()
        self._current_context = context

        # BEFORE_EXECUTE hook
        task = self._run_hook_sync("before_execute", task, context=context)

        try:
            self._active = True
            result = self.execute(task, context)

            # AFTER_EXECUTE hook
            result = self._run_hook_sync(
                "after_execute", result, task=task, context=context
            )

            # Attach execution time if the field exists and isn't already populated.
            elapsed = time.perf_counter() - start
            if hasattr(result, "execution_time"):
                try:
                    current = getattr(result, "execution_time", None)
                    if current is None:
                        result.execution_time = elapsed
                except Exception:
                    pass

            self._last_result = result
            return result

        except Exception as e:
            elapsed = time.perf_counter() - start

            # ON_ERROR hook
            recovery = self._run_hook_sync("error", e, task=task, context=context)

            if isinstance(recovery, AgentResult):
                # Plugin fully handled the error.
                if (
                    hasattr(recovery, "execution_time")
                    and getattr(recovery, "execution_time", None) is None
                ):
                    try:
                        recovery.execution_time = elapsed
                    except Exception:
                        pass
                self._last_result = recovery
                return recovery

            if isinstance(recovery, str):
                # Plugin provided an error message.
                err = AgentResult(success=False, role=self.role, error=recovery)
                if hasattr(err, "execution_time"):
                    try:
                        err.execution_time = elapsed
                    except Exception:
                        pass
                self._last_result = err
                return err

            raise

        finally:
            self._active = False
            self._current_context = None

    @abstractmethod
    def get_system_message(self) -> str:
        """
        Get the role-specific system message.

        Returns:
            System message string for this agent role
        """
        raise NotImplementedError

    # =========================================================================
    # LLM plumbing
    # =========================================================================

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
        user_content_parts: list[str] = []

        if context:
            user_content_parts.append(f"## Previous Context\n{context}\n")

        if additional_context:
            user_content_parts.append(f"## Additional Context\n{additional_context}\n")

        user_content_parts.append(f"## Task\n{task}")

        return [
            {
                "role": "user",
                "type": "message",
                "content": "\n".join(user_content_parts),
            }
        ]

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
            The assistant's response content.

        Notes:
            - Uses a cross-agent lock to avoid concurrent interpreter.chat calls.
            - Skips nested calls within the same thread to avoid re-entrancy hangs.
        """
        # Per-agent recursion guard
        if self._in_llm_call:
            self.log("Skipping LLM call to prevent per-agent nested interpreter hang")
            return ""

        # Cross-agent/thread re-entrancy guard
        if getattr(_LLM_THREAD_STATE, "active", False):
            self.log(
                "Skipping LLM call to prevent nested interpreter.chat in this thread"
            )
            return ""

        self._in_llm_call = True
        try:
            # BEFORE_LLM hook can transform messages and/or system message.
            # Keep these outside the interpreter lock to avoid deadlocks if hooks call back into LLM.
            effective_system = system_message or self.get_system_message()
            messages = self._run_hook_sync(
                "before_llm",
                messages,
                system_message=effective_system,
                timeout=timeout,
            )
            if not isinstance(messages, list):
                # Defensive: plugins should return a messages list.
                messages = list(messages) if messages is not None else []

            response_text = ""

            # Snapshot interpreter state.
            original_system = getattr(self.interpreter, "system_message", None)
            original_auto_run = getattr(self.interpreter, "auto_run", None)
            original_loop = getattr(self.interpreter, "loop", None)
            original_messages = getattr(self.interpreter, "messages", None)

            acquired = _LLM_CALL_LOCK.acquire(timeout=max(1.0, timeout))
            if not acquired:
                self.log("LLM call skipped: could not acquire interpreter lock")
                return ""

            _LLM_THREAD_STATE.active = True
            try:
                # Apply agent settings (under lock).
                try:
                    self.interpreter.system_message = effective_system
                except Exception:
                    pass

                try:
                    self.interpreter.auto_run = True
                except Exception:
                    pass

                try:
                    self.interpreter.loop = False
                except Exception:
                    pass

                try:
                    # Use a shallow copy so agent code won't mutate caller list.
                    self.interpreter.messages = [m.copy() for m in messages]
                except Exception:
                    pass

                # Stream response with timeout protection.
                start = time.perf_counter()
                response_parts: list[str] = []
                gen = None

                try:
                    # NOTE: message=None (not "") to avoid appending empty content
                    # which Anthropic rejects with "text content blocks must be non-empty"
                    gen = self.interpreter.chat(message=None, display=False, stream=True)
                    for chunk in gen:
                        if (time.perf_counter() - start) > timeout:
                            self.log(f"LLM call timed out after {timeout}s")
                            break
                        if not isinstance(chunk, dict):
                            continue
                        if (
                            chunk.get("type") == "message"
                            and chunk.get("role") == "assistant"
                        ):
                            piece = chunk.get("content", "")
                            if piece:
                                response_parts.append(piece)
                finally:
                    # Best effort cleanup of generator.
                    try:
                        if gen is not None:
                            gen.close()
                    except Exception:
                        pass

                response_text = "".join(response_parts)

            finally:
                # Restore interpreter state (still under lock).
                try:
                    if original_system is not None:
                        self.interpreter.system_message = original_system
                except Exception:
                    pass
                try:
                    if original_auto_run is not None:
                        self.interpreter.auto_run = original_auto_run
                except Exception:
                    pass
                try:
                    if original_loop is not None:
                        self.interpreter.loop = original_loop
                except Exception:
                    pass
                try:
                    if original_messages is not None:
                        self.interpreter.messages = original_messages
                except Exception:
                    pass

                _LLM_THREAD_STATE.active = False
                try:
                    _LLM_CALL_LOCK.release()
                except Exception:
                    pass

            # AFTER_LLM hook can post-process response. Also outside lock (same deadlock rationale).
            response_text = self._run_hook_sync(
                "after_llm",
                response_text,
                messages=messages,
                system_message=effective_system,
            )
            return (
                response_text if isinstance(response_text, str) else str(response_text)
            )

        finally:
            self._in_llm_call = False

    # =========================================================================
    # Memory + logging
    # =========================================================================

    def get_memory_context(self, file_path: str | None = None) -> str:
        """
        Get relevant context from semantic memory.

        Args:
            file_path: Optional file to get history for

        Returns:
            Memory context string
        """
        mem = self.memory
        if not mem:
            return ""

        if file_path:
            try:
                return mem.get_institutional_knowledge(file_path)
            except Exception:
                return ""

        return ""

    def log(self, message: str) -> None:
        """Log a message (for debugging)."""
        if getattr(self.interpreter, "verbose", False):
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
