"""
AgentBuilder - Create custom AI coding agents.

Build specialized agents using Open Interpreter as the execution
foundation. Each agent can have:
- Custom system prompts for specialized behavior
- Restricted tool sets for safety/focus
- Shared or isolated memory (SemanticEditGraph)
- Different LLM configurations

Example:
    builder = AgentBuilder()

    # Create a code reviewer agent
    reviewer = builder.create_agent(
        name="reviewer",
        system_prompt="You are a meticulous code reviewer...",
        tools=["read", "grep", "glob"],
    )

    # Create a refactoring agent
    refactorer = builder.create_agent(
        name="refactorer",
        system_prompt="You are an expert at code refactoring...",
        tools=["read", "edit", "write"],
    )

    # Create a swarm for complex tasks
    swarm = builder.create_swarm(
        agents=[reviewer, refactorer],
        orchestrator=SequentialOrchestrator,
    )
"""

import asyncio
import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, Union
from pathlib import Path


class AgentState(Enum):
    """Current state of an agent."""
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class AgentConfig:
    """Configuration for an agent."""
    name: str
    system_prompt: str
    tools: List[str] = field(default_factory=list)
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: int = 300  # seconds
    memory_enabled: bool = True
    memory_path: Optional[str] = None
    context_window: int = 128000
    auto_run: bool = True
    safe_mode: str = "auto"


@dataclass
class AgentResult:
    """Result of an agent execution."""
    success: bool
    output: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    execution_time: float = 0.0
    tokens_used: int = 0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class Agent:
    """
    A specialized AI coding agent built on Open Interpreter.

    Agents are configured with specific capabilities and constraints.
    They can execute tasks, maintain conversation history, and
    optionally share memory with other agents.
    """

    def __init__(
        self,
        config: AgentConfig,
        interpreter: Any = None,
        memory: Any = None,
        plugins: Optional[List["AgentPlugin"]] = None,
    ):
        """
        Initialize an agent.

        Args:
            config: Agent configuration
            interpreter: Optional pre-configured interpreter
            memory: Optional shared SemanticEditGraph
            plugins: Optional list of plugins to activate
        """
        self.config = config
        self.name = config.name
        self._interpreter = interpreter
        self._memory = memory
        self._plugins = plugins or []
        self._state = AgentState.IDLE
        self._history: List[Dict[str, Any]] = []
        self._created_at = datetime.now()

    @property
    def state(self) -> AgentState:
        """Get current agent state."""
        return self._state

    @property
    def interpreter(self):
        """Get or create the interpreter instance."""
        if self._interpreter is None:
            self._interpreter = self._create_interpreter()
        return self._interpreter

    @property
    def memory(self):
        """Get the semantic memory if enabled."""
        if self._memory is None and self.config.memory_enabled:
            # Try to use interpreter's semantic graph
            if hasattr(self.interpreter, 'semantic_graph'):
                self._memory = self.interpreter.semantic_graph
        return self._memory

    def _create_interpreter(self):
        """Create a configured interpreter instance."""
        # Import here to avoid circular imports
        try:
            from interpreter import OpenInterpreter
        except ImportError:
            # Fallback for internal use
            from interpreter.core.core import OpenInterpreter

        interp = OpenInterpreter()

        # Apply configuration
        interp.system_message = self.config.system_prompt

        if self.config.model:
            interp.llm.model = self.config.model
        if self.config.temperature is not None:
            interp.llm.temperature = self.config.temperature
        if self.config.max_tokens:
            interp.llm.max_tokens = self.config.max_tokens
        if self.config.context_window:
            interp.llm.context_window = self.config.context_window

        interp.auto_run = self.config.auto_run
        interp.safe_mode = self.config.safe_mode

        # Configure memory
        if self.config.memory_enabled:
            interp.enable_semantic_memory = True
            if self.config.memory_path:
                interp.semantic_memory_path = self.config.memory_path

        return interp

    async def execute(
        self,
        task: str,
        context: Optional[str] = None,
        stream: bool = False,
    ) -> AgentResult:
        """
        Execute a task.

        Args:
            task: The task description
            context: Optional additional context
            stream: Whether to stream output

        Returns:
            AgentResult with execution details
        """
        self._state = AgentState.RUNNING
        start_time = datetime.now()

        try:
            # Build the full prompt
            full_prompt = task
            if context:
                full_prompt = f"{context}\n\n{task}"

            # Run plugins pre-execution hooks
            for plugin in self._plugins:
                full_prompt = await plugin.on_before_execute(self, full_prompt)

            # Execute via interpreter
            messages = []
            output_parts = []

            for chunk in self.interpreter.chat(full_prompt, stream=True, display=False):
                messages.append(chunk)
                if isinstance(chunk, dict):
                    if chunk.get("type") == "message" and chunk.get("role") == "assistant":
                        content = chunk.get("content", "")
                        if content:
                            output_parts.append(content)
                    elif chunk.get("type") == "console":
                        content = chunk.get("content", "")
                        if content:
                            output_parts.append(f"[output] {content}")

            output = "\n".join(output_parts)
            execution_time = (datetime.now() - start_time).total_seconds()

            result = AgentResult(
                success=True,
                output=output,
                messages=messages,
                execution_time=execution_time,
            )

            # Run plugins post-execution hooks
            for plugin in self._plugins:
                result = await plugin.on_after_execute(self, result)

            self._history.append({
                "task": task,
                "result": result,
                "timestamp": start_time,
            })

            self._state = AgentState.COMPLETED
            return result

        except Exception as e:
            self._state = AgentState.ERROR
            execution_time = (datetime.now() - start_time).total_seconds()
            return AgentResult(
                success=False,
                output="",
                error=str(e),
                execution_time=execution_time,
            )

    def execute_sync(
        self,
        task: str,
        context: Optional[str] = None,
    ) -> AgentResult:
        """
        Synchronous execution wrapper.

        Args:
            task: The task description
            context: Optional additional context

        Returns:
            AgentResult with execution details
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self.execute(task, context))

    def reset(self):
        """Reset agent state and history."""
        self._state = AgentState.IDLE
        if self._interpreter:
            self._interpreter.messages = []

    def get_history(self) -> List[Dict[str, Any]]:
        """Get execution history."""
        return self._history.copy()

    def add_plugin(self, plugin: "AgentPlugin"):
        """Add a plugin to the agent."""
        self._plugins.append(plugin)

    def remove_plugin(self, plugin: "AgentPlugin"):
        """Remove a plugin from the agent."""
        self._plugins.remove(plugin)

    def clone(self, new_name: Optional[str] = None) -> "Agent":
        """
        Create a copy of this agent.

        Args:
            new_name: Optional new name for the clone

        Returns:
            New Agent instance
        """
        new_config = copy.deepcopy(self.config)
        if new_name:
            new_config.name = new_name

        return Agent(
            config=new_config,
            interpreter=None,  # Create fresh interpreter
            memory=self._memory,  # Share memory
            plugins=list(self._plugins),
        )


class Orchestrator(ABC):
    """
    Abstract base for agent orchestration strategies.

    Orchestrators coordinate multiple agents to complete complex tasks.
    """

    @abstractmethod
    async def run(
        self,
        agents: List[Agent],
        task: str,
        shared_memory: Any = None,
    ) -> Dict[str, AgentResult]:
        """
        Orchestrate agents to complete a task.

        Args:
            agents: List of agents to coordinate
            task: The task to complete
            shared_memory: Optional shared memory

        Returns:
            Dict mapping agent names to their results
        """
        pass


class SequentialOrchestrator(Orchestrator):
    """
    Run agents sequentially, passing output as context to next agent.
    """

    async def run(
        self,
        agents: List[Agent],
        task: str,
        shared_memory: Any = None,
    ) -> Dict[str, AgentResult]:
        """Run agents in sequence."""
        results = {}
        context = ""

        for agent in agents:
            result = await agent.execute(task, context=context if context else None)
            results[agent.name] = result

            if result.success:
                context = f"Previous agent ({agent.name}) output:\n{result.output}"
            else:
                # Stop on failure
                break

        return results


class ParallelOrchestrator(Orchestrator):
    """
    Run agents in parallel, aggregate results.
    """

    async def run(
        self,
        agents: List[Agent],
        task: str,
        shared_memory: Any = None,
    ) -> Dict[str, AgentResult]:
        """Run agents in parallel."""
        tasks = [agent.execute(task) for agent in agents]
        agent_results = await asyncio.gather(*tasks, return_exceptions=True)

        results = {}
        for agent, result in zip(agents, agent_results):
            if isinstance(result, Exception):
                results[agent.name] = AgentResult(
                    success=False,
                    output="",
                    error=str(result),
                )
            else:
                results[agent.name] = result

        return results


class PipelineOrchestrator(Orchestrator):
    """
    Run agents as a pipeline where each transforms the output.
    """

    def __init__(self, task_transforms: Optional[Dict[str, Callable[[str, str], str]]] = None):
        """
        Initialize pipeline orchestrator.

        Args:
            task_transforms: Optional dict of agent_name -> transform function
                            that takes (original_task, previous_output) -> new_task
        """
        self.task_transforms = task_transforms or {}

    async def run(
        self,
        agents: List[Agent],
        task: str,
        shared_memory: Any = None,
    ) -> Dict[str, AgentResult]:
        """Run agents as a pipeline."""
        results = {}
        current_task = task
        previous_output = ""

        for agent in agents:
            # Transform task if transformer exists
            if agent.name in self.task_transforms:
                current_task = self.task_transforms[agent.name](task, previous_output)

            result = await agent.execute(current_task, context=previous_output if previous_output else None)
            results[agent.name] = result

            if result.success:
                previous_output = result.output
            else:
                break

        return results


class Swarm:
    """
    A coordinated group of agents working together.

    Swarms use an orchestrator to coordinate agent execution
    and can share memory between agents.
    """

    def __init__(
        self,
        agents: List[Agent],
        orchestrator: Orchestrator,
        shared_memory: Any = None,
        name: Optional[str] = None,
    ):
        """
        Initialize a swarm.

        Args:
            agents: List of agents in the swarm
            orchestrator: Strategy for coordinating agents
            shared_memory: Optional shared SemanticEditGraph
            name: Optional swarm name
        """
        self.agents = agents
        self.orchestrator = orchestrator
        self.shared_memory = shared_memory
        self.name = name or f"swarm_{id(self)}"
        self._results: Dict[str, AgentResult] = {}

    async def execute(self, task: str) -> Dict[str, AgentResult]:
        """
        Execute a task using the swarm.

        Args:
            task: The task to complete

        Returns:
            Dict mapping agent names to their results
        """
        self._results = await self.orchestrator.run(
            self.agents,
            task,
            self.shared_memory,
        )
        return self._results

    def execute_sync(self, task: str) -> Dict[str, AgentResult]:
        """Synchronous execution wrapper."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self.execute(task))

    @property
    def last_results(self) -> Dict[str, AgentResult]:
        """Get results from last execution."""
        return self._results

    def get_agent(self, name: str) -> Optional[Agent]:
        """Get an agent by name."""
        for agent in self.agents:
            if agent.name == name:
                return agent
        return None

    def add_agent(self, agent: Agent):
        """Add an agent to the swarm."""
        self.agents.append(agent)

    def remove_agent(self, name: str) -> bool:
        """Remove an agent by name."""
        for i, agent in enumerate(self.agents):
            if agent.name == name:
                del self.agents[i]
                return True
        return False


class AgentBuilder:
    """
    Factory for creating agents and swarms.

    Provides a convenient API for building specialized agents
    with sensible defaults.
    """

    # Predefined agent templates
    TEMPLATES = {
        "scout": AgentConfig(
            name="scout",
            system_prompt="""You are a codebase exploration specialist.
Your role is to find relevant files, understand project structure, and locate code patterns.
Use search tools efficiently. Summarize findings concisely.
Focus on discovery, not modification.""",
            tools=["read", "glob", "grep", "ls"],
            temperature=0.3,
        ),
        "architect": AgentConfig(
            name="architect",
            system_prompt="""You are a software architect.
Analyze code structure, identify patterns, and understand dependencies.
Focus on the big picture: module relationships, data flow, API contracts.
Provide structural insights, not implementation details.""",
            tools=["read", "glob", "grep"],
            temperature=0.5,
        ),
        "surgeon": AgentConfig(
            name="surgeon",
            system_prompt="""You are a precise code editor.
Make minimal, targeted changes. One edit at a time.
Always verify before modifying. Preserve existing style.
If unsure, ask for clarification rather than guessing.""",
            tools=["read", "edit", "write"],
            temperature=0.2,
        ),
        "reviewer": AgentConfig(
            name="reviewer",
            system_prompt="""You are a meticulous code reviewer.
Find bugs, security issues, and code quality problems.
Be constructive and specific. Cite line numbers.
Prioritize issues by severity. Suggest fixes when possible.""",
            tools=["read", "grep", "glob"],
            temperature=0.4,
        ),
        "tester": AgentConfig(
            name="tester",
            system_prompt="""You are a test engineer.
Write thorough tests. Cover edge cases.
Follow project's testing conventions.
Ensure tests are readable and maintainable.""",
            tools=["read", "write", "bash"],
            temperature=0.3,
        ),
    }

    def __init__(
        self,
        base_interpreter: Any = None,
        shared_memory: Any = None,
        default_model: Optional[str] = None,
    ):
        """
        Initialize the agent builder.

        Args:
            base_interpreter: Optional base interpreter to clone
            shared_memory: Optional shared memory for all agents
            default_model: Default LLM model for agents
        """
        self._base_interpreter = base_interpreter
        self._shared_memory = shared_memory
        self._default_model = default_model
        self._created_agents: List[Agent] = []

    def create_agent(
        self,
        name: str,
        system_prompt: str,
        tools: Optional[List[str]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        memory_enabled: bool = True,
        plugins: Optional[List["AgentPlugin"]] = None,
        **kwargs,
    ) -> Agent:
        """
        Create a custom agent.

        Args:
            name: Agent name
            system_prompt: System message defining behavior
            tools: List of allowed tools
            model: LLM model to use
            temperature: LLM temperature
            memory_enabled: Enable semantic memory
            plugins: Plugins to attach
            **kwargs: Additional AgentConfig fields

        Returns:
            Configured Agent instance
        """
        config = AgentConfig(
            name=name,
            system_prompt=system_prompt,
            tools=tools or [],
            model=model or self._default_model,
            temperature=temperature,
            memory_enabled=memory_enabled,
            **kwargs,
        )

        agent = Agent(
            config=config,
            interpreter=None,  # Create on demand
            memory=self._shared_memory if memory_enabled else None,
            plugins=plugins,
        )

        self._created_agents.append(agent)
        return agent

    def from_template(
        self,
        template_name: str,
        name: Optional[str] = None,
        **overrides,
    ) -> Agent:
        """
        Create an agent from a predefined template.

        Args:
            template_name: Name of the template (scout, architect, etc.)
            name: Optional custom name
            **overrides: Override template settings

        Returns:
            Configured Agent instance
        """
        if template_name not in self.TEMPLATES:
            available = ", ".join(self.TEMPLATES.keys())
            raise ValueError(f"Unknown template '{template_name}'. Available: {available}")

        template = copy.deepcopy(self.TEMPLATES[template_name])

        # Apply overrides
        if name:
            template.name = name
        for key, value in overrides.items():
            if hasattr(template, key):
                setattr(template, key, value)

        if self._default_model and not template.model:
            template.model = self._default_model

        agent = Agent(
            config=template,
            memory=self._shared_memory,
        )

        self._created_agents.append(agent)
        return agent

    def create_swarm(
        self,
        agents: List[Agent],
        orchestrator: Optional[Orchestrator] = None,
        name: Optional[str] = None,
    ) -> Swarm:
        """
        Create a multi-agent swarm.

        Args:
            agents: Agents to include in the swarm
            orchestrator: Coordination strategy (default: Sequential)
            name: Optional swarm name

        Returns:
            Configured Swarm instance
        """
        return Swarm(
            agents=agents,
            orchestrator=orchestrator or SequentialOrchestrator(),
            shared_memory=self._shared_memory,
            name=name,
        )

    def create_standard_swarm(self) -> Swarm:
        """
        Create a standard development swarm with Scout, Architect, Surgeon.

        Returns:
            Swarm with standard development agents
        """
        scout = self.from_template("scout")
        architect = self.from_template("architect")
        surgeon = self.from_template("surgeon")

        return self.create_swarm(
            agents=[scout, architect, surgeon],
            orchestrator=SequentialOrchestrator(),
            name="standard_dev_swarm",
        )

    def get_created_agents(self) -> List[Agent]:
        """Get all agents created by this builder."""
        return self._created_agents.copy()


# Plugin interface (defined here for type hints, full impl in plugins.py)
class AgentPlugin(ABC):
    """Base class for agent plugins."""

    @abstractmethod
    async def on_before_execute(self, agent: Agent, task: str) -> str:
        """Called before agent executes. Can modify task."""
        pass

    @abstractmethod
    async def on_after_execute(self, agent: Agent, result: AgentResult) -> AgentResult:
        """Called after agent executes. Can modify result."""
        pass
