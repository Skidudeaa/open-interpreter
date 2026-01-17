"""
AgentOrchestrator - Coordinates multiple specialized agents.

Routes tasks to appropriate agents and manages the workflow:
1) Scout: find relevant files and code
2) Architect: analyze structure (optional)
3) Surgeon: propose precise edits
4) Validator: test/validate changes

The orchestrator selects the smallest workflow that matches user intent and context.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .base_agent import AgentResult, AgentRole, BaseAgent

logger = logging.getLogger(__name__)

# Import UI event system for agent visualization (optional).
HAS_UI_EVENTS = False

try:
    from ...terminal_interface.components.activity_stream import emit_activity
    from ...terminal_interface.components.ui_events import (
        EventBus,
        EventType,
        UIEvent,
        get_event_bus,
    )
    from ...terminal_interface.components.ui_state import AgentRole as UIAgentRole

    HAS_UI_EVENTS = True
except ImportError:
    # UI is optional. Keep the orchestrator functional without it.
    def emit_activity(*args, **kwargs):
        pass


if TYPE_CHECKING:
    from ..core import OpenInterpreter
    from ..memory import SemanticEditGraph


class WorkflowType(Enum):
    """Pre-defined workflow types."""

    NONE = "none"  # No agent needed, use LLM directly
    EXPLORE = "explore"  # Scout only
    EDIT = "edit"  # Scout -> Surgeon
    FULL = "full"  # Scout -> Architect -> Surgeon -> Validator
    VALIDATE = "validate"  # Validator only


@dataclass(slots=True)
class WorkflowResult:
    """Result from a complete workflow."""

    workflow_type: WorkflowType
    success: bool = False
    agent_results: dict[AgentRole, AgentResult] = field(default_factory=dict)
    total_duration_ms: float = 0.0
    final_context: str = ""
    errors: list[str] = field(default_factory=list)

    def get_summary(self) -> str:
        """Get a summary of the workflow result."""
        lines = [
            f"## Workflow: {self.workflow_type.value}",
            f"Status: {'SUCCESS' if self.success else 'FAILED'}",
            f"Duration: {self.total_duration_ms:.0f}ms",
            "",
            "### Agent Results:",
        ]

        for role, result in self.agent_results.items():
            status = "OK" if result.success else "FAILED"
            lines.append(f"- {role.value}: {status}")

        if self.errors:
            lines.append("")
            lines.append("### Errors:")
            for error in self.errors:
                lines.append(f"- {error}")

        return "\n".join(lines)


_PROJECT_MARKERS: tuple[str, ...] = (
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "setup.py",
    "requirements.txt",
    ".project",
    "pom.xml",
    "build.gradle",
)


def _detect_project_root(start_path: str) -> str:
    """
    Detect project root by walking upwards for common project markers.

    WHAT: Returns the nearest ancestor directory containing a known marker,
          else returns the (absolute) starting directory.

    WHY: Keeps Scout from treating the entire home directory as a codebase
         when invoked from an arbitrary working directory.

    Args:
        start_path: Starting path to search from (file or directory)

    Returns:
        Absolute project root path (directory).
    """
    start = Path(start_path).expanduser()
    try:
        start = start.resolve()
    except OSError:
        # Some environments (containers, permission weirdness) can fail resolve().
        start = Path(os.path.abspath(str(start)))

    if start.is_file():
        start = start.parent

    # Only cap traversal at HOME if the start path is actually inside HOME.
    home = Path.home()
    try:
        home = home.resolve()
    except OSError:
        home = Path(os.path.abspath(str(home)))

    limit_to_home = start.is_relative_to(home)

    current = start
    while True:
        if any((current / m).exists() for m in _PROJECT_MARKERS):
            return str(current)

        parent = current.parent
        if parent == current:
            break  # filesystem root

        if limit_to_home and not parent.is_relative_to(home):
            break

        current = parent

    return str(start)


class AgentOrchestrator:
    """
    Coordinates specialized agents for codebase tasks.

    Workflow roles:
      1) Scout: locate relevant files / code
      2) Architect: summarize structure (optional)
      3) Surgeon: propose precise edits
      4) Validator: run checks/tests

    The orchestrator selects a workflow based on the task and available context.
    """

    _ROLE_ACTIVITY = {
        AgentRole.SCOUT: ("search", "Searching codebase"),
        AgentRole.SURGEON: ("edit", "Preparing edits"),
        AgentRole.ARCHITECT: ("plan", "Analyzing architecture"),
        AgentRole.VALIDATOR: ("validate", "Running validation"),
    }

    # WHY: Per-agent model configuration for specialized tasks
    # TRADEOFF: Scout uses fast model for exploration, Surgeon uses powerful model for precision
    # NOTE: None means use interpreter's default model
    # NOTE: Model names must include provider prefix for litellm routing (e.g., "anthropic/", "gemini/")
    _ROLE_MODELS: dict[AgentRole, str | None] = {
        AgentRole.SCOUT: "gemini/gemini-3-flash-preview",  # Fast for exploration
        AgentRole.SURGEON: "claude-opus-4-5-20251101",  # Precise for edits
        AgentRole.ARCHITECT: "claude-opus-4-5-20251101",  # Deep analysis
        AgentRole.VALIDATOR: None,  # Use default
    }

    def __init__(
        self,
        interpreter: "OpenInterpreter",
        memory: Optional["SemanticEditGraph"] = None,
        root_path: str | None = None,
        event_bus: Optional["EventBus"] = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            interpreter: The OpenInterpreter instance
            memory: Optional shared semantic memory
            root_path: Root path for file operations (auto-detected if not provided)
            event_bus: Optional EventBus for UI updates
        """
        self.interpreter = interpreter
        self.memory = (
            memory
            if memory is not None
            else getattr(interpreter, "semantic_graph", None)
        )

        # Auto-detect project root if not explicitly provided.
        if root_path:
            self.root_path = os.path.abspath(root_path)
        else:
            self.root_path = _detect_project_root(os.getcwd())

        # Lazy-loaded agents.
        self._agents: dict[AgentRole, BaseAgent] = {}

        # Event bus for UI updates (optional).
        self.event_bus = event_bus
        if self.event_bus is None and HAS_UI_EVENTS:
            self.event_bus = get_event_bus()

        # Monotonic id generator for UI linking.
        self._agent_id_seq = count(1)

        # WHY: Concurrent agents could clobber each other's model settings.
        # This lock ensures model switching is atomic.
        # TRADEOFF: Serializes agent execution, but prevents race conditions.
        self._model_switch_lock = threading.Lock()

        # WHY: _detect_workflow() makes LLM call every iteration, even on loop-back.
        # Cache workflow decisions keyed by task hash to avoid redundant calls.
        # TRADEOFF: Small memory overhead vs. repeated LLM calls.
        self._workflow_cache: dict[str, WorkflowType] = {}

    @staticmethod
    def _preview(text: object, limit: int = 200) -> str:
        """Return a safe, short preview string for UI display."""
        try:
            s = str(text)
        except Exception:
            return "<unprintable>"
        if len(s) <= limit:
            return s
        return s[: limit - 3] + "..."

    def _emit_agent_event(
        self, event_type: "EventType", agent_id: str, role: AgentRole, **data
    ):
        """
        Emit an agent event to the UI.

        WHY: UI event emission is optional. When the UI isn't installed, this is a no-op.
        """
        if not HAS_UI_EVENTS or not self.event_bus:
            return

        ui_role = UIAgentRole.from_core_role(role)
        event_data = {"agent_id": agent_id, "role": ui_role.value, **data}
        event = UIEvent(type=event_type, data=event_data, source="orchestrator")
        self.event_bus.emit(event)

    def _generate_agent_id(self, role: AgentRole) -> str:
        """Generate a unique agent ID."""
        return f"{role.value}-{next(self._agent_id_seq)}"

    def _execute_agent_with_events(
        self,
        role: AgentRole,
        task: str,
        context: str | None = None,
        parent_id: str | None = None,
    ) -> tuple[str, AgentResult]:
        """
        Execute an agent with UI + activity stream hooks.

        Returns:
            (agent_id, agent_result)
        """
        agent_id = self._generate_agent_id(role)

        if HAS_UI_EVENTS and self.event_bus:
            self._emit_agent_event(
                EventType.AGENT_SPAWN,
                agent_id,
                role,
                task=task,
                parent_id=parent_id,
            )

        activity_type, activity_msg = self._ROLE_ACTIVITY.get(
            role, ("think", "Processing")
        )
        emit_activity(
            activity_type,
            activity_msg,
            task[:40] + "..." if len(task) > 40 else task,
            agent=role.value,
        )

        agent = self.get_agent(role)

        # WHY: Swap model for role-specific LLM configuration
        # TRADEOFF: Slight overhead from model switching vs unified config
        # NOTE: Lock prevents concurrent agents from clobbering each other's model
        role_model = self._ROLE_MODELS.get(role)
        original_model = None

        # Acquire lock for model switching to prevent race conditions
        with self._model_switch_lock:
            if role_model is not None:
                original_model = self.interpreter.llm.model
                self.interpreter.llm.model = role_model
                logger.debug(f"Switched model to {role_model} for {role.value}")

            try:
                agent_result = agent.run(task, context=context)

                if HAS_UI_EVENTS and self.event_bus:
                    if agent_result.success:
                        self._emit_agent_event(
                            EventType.AGENT_COMPLETE,
                            agent_id,
                            role,
                            result=self._preview(agent_result.content),
                        )
                    else:
                        self._emit_agent_event(
                            EventType.AGENT_ERROR,
                            agent_id,
                            role,
                            error=f"{role.value} execution failed",
                        )

                return agent_id, agent_result

            except Exception as e:
                if HAS_UI_EVENTS and self.event_bus:
                    self._emit_agent_event(
                        EventType.AGENT_ERROR,
                        agent_id,
                        role,
                        error=str(e),
                    )
                raise
            finally:
                # Restore original model after agent execution
                if original_model is not None:
                    self.interpreter.llm.model = original_model
                    logger.debug(f"Restored model to {original_model}")

    def get_agent(self, role: AgentRole) -> BaseAgent:
        """Get or create an agent by role."""
        agent = self._agents.get(role)
        if agent is None:
            agent = self._create_agent(role)
            self._agents[role] = agent
        return agent

    def _create_agent(self, role: AgentRole) -> BaseAgent:
        """
        Create an agent for the given role.

        WHY: Agents receive a reference to the orchestrator so they can ask
             other agents for context without tight coupling.
        """
        from .architect_agent import ArchitectAgent
        from .scout_agent import ScoutAgent
        from .surgeon_agent import SurgeonAgent
        from .validator_agent import ValidatorAgent

        agent_classes = {
            AgentRole.SCOUT: ScoutAgent,
            AgentRole.SURGEON: SurgeonAgent,
            AgentRole.ARCHITECT: ArchitectAgent,
            AgentRole.VALIDATOR: ValidatorAgent,
        }

        agent_class = agent_classes.get(role)
        if not agent_class:
            raise ValueError(f"No agent implementation for role: {role}")

        agent = agent_class(
            interpreter=self.interpreter,
            memory=self.memory,
            root_path=self.root_path,
        )
        agent._orchestrator = self  # Inter-agent communication hook.
        return agent

    def handle_task(
        self,
        task: str,
        workflow: WorkflowType | None = None,
        auto_apply: bool = False,
    ) -> WorkflowResult:
        """
        Handle a task using the selected workflow.

        Args:
            task: Task description
            workflow: Workflow type (auto-detected if None)
            auto_apply: Apply edits to disk when True

        Returns:
            WorkflowResult
        """
        start = time.perf_counter()

        if workflow is None:
            workflow = self._detect_workflow(task)
            if workflow != WorkflowType.NONE:
                emit_activity(
                    "plan",
                    f"Routing to {workflow.value} workflow",
                    task[:50] + "..." if len(task) > 50 else task,
                )

        result = WorkflowResult(workflow_type=workflow)

        try:
            if workflow == WorkflowType.NONE:
                # Intentionally do nothing: caller should use the base LLM path.
                pass
            elif workflow == WorkflowType.EXPLORE:
                self._run_explore_workflow(task, result)
            elif workflow == WorkflowType.EDIT:
                self._run_edit_workflow(task, result, auto_apply)
            elif workflow == WorkflowType.FULL:
                self._run_full_workflow(task, result, auto_apply)
            elif workflow == WorkflowType.VALIDATE:
                self._run_validate_workflow(task, result)
            else:
                result.errors.append(f"Unknown workflow: {workflow}")
        except Exception as e:
            result.errors.append(str(e))

        result.total_duration_ms = (time.perf_counter() - start) * 1000.0
        result.final_context = self._build_final_context(result)
        result.success = (not result.errors) and all(
            r.success for r in result.agent_results.values()
        )
        return result

    def _detect_workflow(self, task: str) -> WorkflowType:
        """
        Use LLM to intelligently detect the appropriate workflow.

        WHY: Keyword matching was dumb and caused misroutes. The LLM understands
        intent far better than pattern matching on "fix", "codebase", etc.

        TRADEOFF: One fast LLM call vs brittle keyword heuristics.

        # NOTE: Results are cached by task hash to avoid repeated LLM calls on
        # loop-back iterations. Cache invalidates on new task content.
        """
        # Very short messages don't need agent routing
        if len(task.strip()) < 15:
            return WorkflowType.NONE

        # Check cache to avoid repeated LLM calls
        # WHY: Same task resubmitted in loop should not re-call LLM
        # TRADEOFF: Small memory overhead vs. repeated LLM calls
        import hashlib

        task_hash = hashlib.md5(task.encode("utf-8")).hexdigest()
        if task_hash in self._workflow_cache:
            cached = self._workflow_cache[task_hash]
            logger.debug(
                f"Workflow cache hit: {cached.name} for task hash {task_hash[:8]}"
            )
            return cached

        prompt = f"""Classify this user request into exactly ONE workflow type.

User request: {task[:500]}

Workflow types:
- NONE: Pure conversation, general questions, math, or non-code requests
- EXPLORE: Finding, searching, listing, locating, or understanding files/code (read-only)
- EDIT: Modifying, fixing, adding, or changing code
- VALIDATE: Running tests or verifying something works
- FULL: Complex multi-step task requiring exploration, editing, AND validation

Rules:
- EXPLORE for ANY file/code search: "find files", "search for", "where is", "list all", "grep", "locate"
- EDIT for code changes: fix, add, refactor, implement, update, create, remove
- VALIDATE for testing: run tests, verify, check if works
- FULL only for large features needing all steps
- NONE only for chat/questions that don't involve finding or modifying files

Examples:
- "find all JavaScript files" → EXPLORE
- "where is auth handled" → EXPLORE
- "search for ProseMirror" → EXPLORE
- "what files use X" → EXPLORE
- "fix the bug in login" → EDIT
- "hello, how are you?" → NONE

Respond with exactly one word: NONE, EXPLORE, EDIT, VALIDATE, or FULL"""

        def _cache_and_return(workflow: WorkflowType) -> WorkflowType:
            """Cache the workflow result and return it."""
            self._workflow_cache[task_hash] = workflow
            # Limit cache size to prevent unbounded growth
            if len(self._workflow_cache) > 100:
                # Remove oldest entries (arbitrary pruning)
                oldest = list(self._workflow_cache.keys())[:50]
                for key in oldest:
                    del self._workflow_cache[key]
            return workflow

        try:
            if not (self.interpreter and hasattr(self.interpreter, "llm")):
                logger.warning("Workflow detection skipped: no LLM available")
                return _cache_and_return(WorkflowType.NONE)

            # WHY: LiteLLM/OpenAI requires first message to have 'system' role
            messages = [{"role": "system", "type": "message", "content": prompt}]
            response_text = ""
            for chunk in self.interpreter.llm.run(messages):
                if chunk.get("type") == "message" and chunk.get("content"):
                    response_text += chunk["content"]
                # Stop early once we have enough
                if len(response_text) > 20:
                    break

            # Check for empty response
            if not response_text.strip():
                logger.warning("Workflow detection: LLM returned empty response")
                return _cache_and_return(WorkflowType.NONE)

            # Parse response
            response_upper = response_text.strip().upper()
            for wf in WorkflowType:
                if wf.name in response_upper:
                    logger.info(
                        f"Workflow detected: {wf.name} (response: '{response_text.strip()[:30]}')"
                    )
                    return _cache_and_return(wf)

            # No match found
            logger.warning(
                f"Workflow detection: no match in response '{response_text.strip()[:50]}'"
            )
            return _cache_and_return(WorkflowType.NONE)

        except Exception as e:
            logger.warning(f"LLM workflow detection failed: {e}")
            return _cache_and_return(WorkflowType.NONE)

    def _apply_pending_edits(self, result: WorkflowResult) -> None:
        """
        Apply any pending edits produced by the Surgeon.

        WHY: Centralizes error handling + activity stream emission.
        """
        surgeon = self.get_agent(AgentRole.SURGEON)
        pending_edits = surgeon.get_pending_edits()
        if not pending_edits:
            return

        emit_activity("edit", f"Applying {len(pending_edits)} edit(s)", agent="surgeon")
        for edit in pending_edits:
            emit_activity("edit", "Writing changes", edit.file_path, agent="surgeon")
            try:
                ok = surgeon.apply_edit(edit)
            except Exception as e:
                ok = False
                result.errors.append(
                    f"Exception applying edit to {edit.file_path}: {e}"
                )
            if not ok:
                result.errors.append(f"Failed to apply edit to {edit.file_path}")

    def _run_explore_workflow(self, task: str, result: WorkflowResult) -> None:
        """Scout-only workflow.

        WHY: Scout returns structured findings (files, symbols, code matches).
        The main LLM in respond.py receives these as context and synthesizes
        a natural response. This avoids an extra LLM call here.

        TRADEOFF: Removed _synthesize_for_user() - main LLM handles synthesis.
        """
        _, scout_result = self._execute_agent_with_events(AgentRole.SCOUT, task)
        result.agent_results[AgentRole.SCOUT] = scout_result
        if not scout_result.success:
            result.errors.append("Scout phase failed")

    def _run_edit_workflow(
        self, task: str, result: WorkflowResult, auto_apply: bool
    ) -> None:
        """Scout -> Surgeon workflow."""
        scout_id, scout_result = self._execute_agent_with_events(AgentRole.SCOUT, task)
        result.agent_results[AgentRole.SCOUT] = scout_result
        if not scout_result.success:
            result.errors.append("Scout phase failed")
            return

        _, surgeon_result = self._execute_agent_with_events(
            AgentRole.SURGEON,
            task,
            context=scout_result.context_for_next,
            parent_id=scout_id,
        )
        result.agent_results[AgentRole.SURGEON] = surgeon_result
        if not surgeon_result.success:
            result.errors.append("Surgeon phase failed")
            return

        if auto_apply:
            self._apply_pending_edits(result)

    def _run_full_workflow(
        self, task: str, result: WorkflowResult, auto_apply: bool
    ) -> None:
        """Scout -> Architect -> Surgeon -> (apply) -> Validator workflow."""
        scout_id, scout_result = self._execute_agent_with_events(AgentRole.SCOUT, task)
        result.agent_results[AgentRole.SCOUT] = scout_result
        if not scout_result.success:
            result.errors.append("Scout phase failed")
            return

        architect_id, architect_result = self._execute_agent_with_events(
            AgentRole.ARCHITECT,
            task,
            context=scout_result.context_for_next,
            parent_id=scout_id,
        )
        result.agent_results[AgentRole.ARCHITECT] = architect_result
        if not architect_result.success:
            result.errors.append("Architect phase failed")
            return

        context = architect_result.context_for_next or scout_result.context_for_next

        surgeon_id, surgeon_result = self._execute_agent_with_events(
            AgentRole.SURGEON,
            task,
            context=context,
            parent_id=architect_id,
        )
        result.agent_results[AgentRole.SURGEON] = surgeon_result
        if not surgeon_result.success:
            result.errors.append("Surgeon phase failed")
            return

        if auto_apply:
            self._apply_pending_edits(result)
            _, validator_result = self._execute_agent_with_events(
                AgentRole.VALIDATOR,
                f"Validate edits for: {task}",
                context=surgeon_result.context_for_next,
                parent_id=surgeon_id,
            )
            result.agent_results[AgentRole.VALIDATOR] = validator_result
            if not validator_result.success:
                result.errors.append("Validator phase failed")

    def _run_validate_workflow(self, task: str, result: WorkflowResult) -> None:
        """Validator-only workflow."""
        _, validator_result = self._execute_agent_with_events(AgentRole.VALIDATOR, task)
        result.agent_results[AgentRole.VALIDATOR] = validator_result
        if not validator_result.success:
            result.errors.append("Validator phase failed")

    def _build_final_context(self, result: WorkflowResult) -> str:
        """Build a combined context from all agent results."""
        parts = [f"# Workflow Result: {result.workflow_type.value}"]

        for role, agent_result in result.agent_results.items():
            parts.append(f"\n## {role.value.title()} Agent")
            parts.append(agent_result.to_context_string())

        if result.errors:
            parts.append("\n## Errors")
            for error in result.errors:
                parts.append(f"- {error}")

        return "\n".join(parts)


# Convenience function
def orchestrate(interpreter: "OpenInterpreter", task: str, **kwargs) -> WorkflowResult:
    """
    Run an orchestrated workflow.

    Args:
        interpreter: The OpenInterpreter instance
        task: The task to perform
        **kwargs: Additional arguments for AgentOrchestrator.handle_task

    Returns:
        WorkflowResult
    """
    orchestrator = AgentOrchestrator(interpreter)
    return orchestrator.handle_task(task, **kwargs)
