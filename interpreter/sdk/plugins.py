"""
AgentPlugin - Extend agent behavior with hooks.

Plugins allow you to intercept and modify agent behavior at key points:
- Before LLM calls (modify context)
- After execution (process results)
- On edits (validate or transform edits)
- On errors (handle or recover)

Example:
    class LoggingPlugin(AgentPlugin):
        async def on_before_execute(self, agent, task):
            print(f"[{agent.name}] Starting: {task[:50]}...")
            return task

        async def on_after_execute(self, agent, result):
            print(f"[{agent.name}] Completed: success={result.success}")
            return result

    agent = builder.create_agent(
        name="my-agent",
        system_prompt="...",
        plugins=[LoggingPlugin()],
    )
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, Union


class HookPoint(Enum):
    """Points where plugins can intercept execution."""
    BEFORE_EXECUTE = "before_execute"
    AFTER_EXECUTE = "after_execute"
    BEFORE_LLM = "before_llm"
    AFTER_LLM = "after_llm"
    BEFORE_EDIT = "before_edit"
    AFTER_EDIT = "after_edit"
    ON_ERROR = "on_error"
    ON_TOOL_CALL = "on_tool_call"


@dataclass
class PluginContext:
    """
    Context passed to plugin hooks.

    Contains information about the current execution state
    that plugins can use to make decisions.
    """
    agent_name: str
    hook_point: HookPoint
    timestamp: datetime = field(default_factory=datetime.now)
    task: Optional[str] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    current_file: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_metadata(self, key: str, value: Any):
        """Add metadata to context."""
        self.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get metadata from context."""
        return self.metadata.get(key, default)


@dataclass
class EditContext:
    """Context for edit-related hooks."""
    file_path: str
    original_content: str
    new_content: str
    edit_type: str = "unknown"
    symbols_affected: List[str] = field(default_factory=list)


class AgentPlugin(ABC):
    """
    Base class for agent plugins.

    Plugins extend agent behavior by hooking into execution lifecycle.
    All hooks are async to support non-blocking operations.

    Implement only the hooks you need - default implementations pass through.
    """

    name: str = "base_plugin"
    priority: int = 100  # Lower = runs first

    async def on_before_execute(self, agent: Any, task: str) -> str:
        """
        Called before agent executes a task.

        Args:
            agent: The executing agent
            task: The task string

        Returns:
            Modified task string (or original)
        """
        return task

    async def on_after_execute(self, agent: Any, result: Any) -> Any:
        """
        Called after agent completes execution.

        Args:
            agent: The executing agent
            result: The AgentResult

        Returns:
            Modified result (or original)
        """
        return result

    async def on_before_llm(self, agent: Any, messages: List[Dict]) -> List[Dict]:
        """
        Called before LLM is invoked.

        Args:
            agent: The agent
            messages: Messages to send to LLM

        Returns:
            Modified messages (or original)
        """
        return messages

    async def on_after_llm(self, agent: Any, response: Dict) -> Dict:
        """
        Called after LLM responds.

        Args:
            agent: The agent
            response: LLM response

        Returns:
            Modified response (or original)
        """
        return response

    async def on_before_edit(self, agent: Any, edit_context: EditContext) -> EditContext:
        """
        Called before a code edit is applied.

        Args:
            agent: The agent
            edit_context: Edit details

        Returns:
            Modified edit context (or original). Return None to cancel edit.
        """
        return edit_context

    async def on_after_edit(self, agent: Any, edit_context: EditContext, success: bool) -> None:
        """
        Called after a code edit is applied.

        Args:
            agent: The agent
            edit_context: Edit details
            success: Whether the edit succeeded
        """
        pass

    async def on_error(self, agent: Any, error: Exception, context: PluginContext) -> Optional[str]:
        """
        Called when an error occurs.

        Args:
            agent: The agent
            error: The exception
            context: Current plugin context

        Returns:
            Recovery action string, or None to propagate error
        """
        return None

    async def on_tool_call(self, agent: Any, tool_name: str, args: Dict) -> Dict:
        """
        Called when a tool is invoked.

        Args:
            agent: The agent
            tool_name: Name of the tool
            args: Tool arguments

        Returns:
            Modified arguments (or original)
        """
        return args

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(priority={self.priority})>"


class PluginRegistry:
    """
    Registry for managing plugins.

    Handles plugin registration, ordering by priority,
    and coordinated hook invocation.
    """

    def __init__(self):
        self._plugins: List[AgentPlugin] = []
        self._by_hook: Dict[HookPoint, List[AgentPlugin]] = {
            hook: [] for hook in HookPoint
        }

    def register(self, plugin: AgentPlugin) -> None:
        """
        Register a plugin.

        Args:
            plugin: Plugin to register
        """
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: p.priority)

        # Re-index by hook point
        self._reindex()

    def unregister(self, plugin: AgentPlugin) -> bool:
        """
        Unregister a plugin.

        Args:
            plugin: Plugin to remove

        Returns:
            True if plugin was found and removed
        """
        if plugin in self._plugins:
            self._plugins.remove(plugin)
            self._reindex()
            return True
        return False

    def unregister_by_name(self, name: str) -> int:
        """
        Unregister all plugins with a given name.

        Args:
            name: Plugin name to remove

        Returns:
            Number of plugins removed
        """
        original_count = len(self._plugins)
        self._plugins = [p for p in self._plugins if p.name != name]
        removed = original_count - len(self._plugins)
        if removed > 0:
            self._reindex()
        return removed

    def get_plugins(self) -> List[AgentPlugin]:
        """Get all registered plugins."""
        return self._plugins.copy()

    def get_plugins_for_hook(self, hook: HookPoint) -> List[AgentPlugin]:
        """Get plugins that implement a specific hook."""
        return self._by_hook.get(hook, [])

    def _reindex(self) -> None:
        """Rebuild the by-hook index."""
        for hook in HookPoint:
            self._by_hook[hook] = []

        for plugin in self._plugins:
            # Check which hooks are overridden
            for hook in HookPoint:
                method_name = f"on_{hook.value}"
                if hasattr(plugin, method_name):
                    method = getattr(plugin, method_name)
                    # Check if method is overridden from base
                    base_method = getattr(AgentPlugin, method_name, None)
                    if base_method is None or method.__func__ is not base_method:
                        self._by_hook[hook].append(plugin)

    async def run_hook(
        self,
        hook: HookPoint,
        agent: Any,
        value: Any,
        **kwargs,
    ) -> Any:
        """
        Run all plugins for a hook point.

        Args:
            hook: The hook point
            agent: The agent
            value: The value to transform
            **kwargs: Additional arguments

        Returns:
            Transformed value after all plugins
        """
        plugins = self._by_hook.get(hook, [])

        for plugin in plugins:
            method_name = f"on_{hook.value}"
            method = getattr(plugin, method_name, None)
            if method:
                result = await method(agent, value, **kwargs)
                if result is not None:
                    value = result

        return value


# Built-in plugins

class LoggingPlugin(AgentPlugin):
    """
    Logs agent activity.
    """
    name = "logging"
    priority = 10  # Run early

    def __init__(self, log_func: Optional[Callable[[str], None]] = None):
        self.log = log_func or print

    async def on_before_execute(self, agent: Any, task: str) -> str:
        self.log(f"[{datetime.now().isoformat()}] {agent.name} START: {task[:100]}...")
        return task

    async def on_after_execute(self, agent: Any, result: Any) -> Any:
        status = "SUCCESS" if result.success else "FAILED"
        self.log(f"[{datetime.now().isoformat()}] {agent.name} {status} ({result.execution_time:.2f}s)")
        return result

    async def on_error(self, agent: Any, error: Exception, context: PluginContext) -> Optional[str]:
        self.log(f"[{datetime.now().isoformat()}] {agent.name} ERROR: {error}")
        return None


class MetricsPlugin(AgentPlugin):
    """
    Collects execution metrics.
    """
    name = "metrics"
    priority = 5  # Run very early

    def __init__(self):
        self.metrics: Dict[str, List[Dict]] = {}

    async def on_before_execute(self, agent: Any, task: str) -> str:
        if agent.name not in self.metrics:
            self.metrics[agent.name] = []
        return task

    async def on_after_execute(self, agent: Any, result: Any) -> Any:
        self.metrics[agent.name].append({
            "timestamp": datetime.now().isoformat(),
            "success": result.success,
            "execution_time": result.execution_time,
            "tokens_used": result.tokens_used,
        })
        return result

    def get_summary(self, agent_name: Optional[str] = None) -> Dict:
        """Get metrics summary."""
        if agent_name:
            runs = self.metrics.get(agent_name, [])
        else:
            runs = [r for agent_runs in self.metrics.values() for r in agent_runs]

        if not runs:
            return {"total_runs": 0}

        success_count = sum(1 for r in runs if r["success"])
        total_time = sum(r["execution_time"] for r in runs)

        return {
            "total_runs": len(runs),
            "success_rate": success_count / len(runs),
            "total_time": total_time,
            "avg_time": total_time / len(runs),
        }


class ValidationPlugin(AgentPlugin):
    """
    Validates edits before applying.
    """
    name = "validation"
    priority = 50

    def __init__(self, validator: Optional[Any] = None):
        self._validator = validator

    @property
    def validator(self):
        if self._validator is None:
            try:
                from interpreter.core.validation import EditValidator
                self._validator = EditValidator()
            except ImportError:
                pass
        return self._validator

    async def on_before_edit(self, agent: Any, edit_context: EditContext) -> EditContext:
        if self.validator is None:
            return edit_context

        result = self.validator.validate_syntax_only(
            edit_context.file_path,
            edit_context.new_content,
        )

        if not result.valid:
            # Cancel the edit by returning None
            return None

        return edit_context


class MemoryPlugin(AgentPlugin):
    """
    Records edits to semantic memory.
    """
    name = "memory"
    priority = 90  # Run late

    def __init__(self, semantic_graph: Optional[Any] = None):
        self._graph = semantic_graph

    async def on_after_edit(self, agent: Any, edit_context: EditContext, success: bool) -> None:
        if not success or self._graph is None:
            return

        try:
            from interpreter.core.memory import Edit, EditType, DiffSymbolExtractor

            # Create edit record
            extractor = DiffSymbolExtractor()
            _, modified, _ = extractor.find_affected_symbols(
                edit_context.original_content,
                edit_context.new_content,
                edit_context.file_path,
            )

            edit = Edit(
                file_path=edit_context.file_path,
                original_content=edit_context.original_content,
                new_content=edit_context.new_content,
                edit_type=EditType.UNKNOWN,
                affected_symbols=modified,
            )

            self._graph.record_edit(edit)

        except Exception:
            pass  # Don't fail on memory errors


class RateLimitPlugin(AgentPlugin):
    """
    Rate limits agent execution.
    """
    name = "rate_limit"
    priority = 1  # Run first

    def __init__(
        self,
        max_calls_per_minute: int = 60,
        max_tokens_per_minute: int = 100000,
    ):
        self.max_calls = max_calls_per_minute
        self.max_tokens = max_tokens_per_minute
        self._call_times: List[datetime] = []
        self._token_counts: List[tuple] = []  # (timestamp, tokens)

    async def on_before_execute(self, agent: Any, task: str) -> str:
        import asyncio

        now = datetime.now()
        minute_ago = now.timestamp() - 60

        # Clean old entries
        self._call_times = [t for t in self._call_times if t.timestamp() > minute_ago]
        self._token_counts = [(t, c) for t, c in self._token_counts if t.timestamp() > minute_ago]

        # Check rate limit
        if len(self._call_times) >= self.max_calls:
            wait_time = 60 - (now.timestamp() - self._call_times[0].timestamp())
            await asyncio.sleep(max(0, wait_time))

        self._call_times.append(now)
        return task

    async def on_after_execute(self, agent: Any, result: Any) -> Any:
        if result.tokens_used > 0:
            self._token_counts.append((datetime.now(), result.tokens_used))
        return result
