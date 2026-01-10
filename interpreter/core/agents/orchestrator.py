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
import re
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

_CODE_EXT_RE = re.compile(
    r"(?i)\.(py|js|ts|jsx|tsx|go|rs|rb|java|cpp|c|h|hpp|cs|swift|kt|php|scala|ex|exs|clj|hs|ml)\b"
)
_NON_CODE_EXT_RE = re.compile(
    r"(?i)\.(png|jpe?g|gif|svg|ico|mp3|mp4|wav|pdf|zip|tar)\b"
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
        Detect the appropriate workflow from a task.

        WHY: Keyword routing is cheap and predictable.
        TRADEOFF: Misroutes are possible; we bias toward explicit user intent.
        """
        task_lower = task.lower()

        # Extract user intent BEFORE @file expansion.
        if "@" in task_lower:
            before_at = task_lower.split("@", 1)[0].strip()
            user_intent = before_at or task_lower[:100]
        else:
            user_intent = task_lower[:100]

        strong_explore = (
            "review",
            "explain",
            "analyze",
            "examine",
            "look at",
            "walk through",
        )
        strong_edit = ("fix", "refactor", "rewrite", "implement", "add feature")
        strong_validate = ("run tests", "test this", "verify", "validate", "test ")

        # Strong intent words override length heuristics.
        if any(kw in user_intent for kw in strong_explore):
            return WorkflowType.EXPLORE
        if any(kw in user_intent for kw in strong_validate):
            return WorkflowType.VALIDATE
        if any(kw in user_intent for kw in strong_edit):
            return WorkflowType.EDIT

        # Skip agent routing for genuinely short/simple messages.
        if len(user_intent) < 30:
            return WorkflowType.NONE

        has_code_file = bool(_CODE_EXT_RE.search(task_lower))
        has_non_code_file = bool(_NON_CODE_EXT_RE.search(task_lower))

        code_keywords = (
            "function",
            "class",
            "method",
            "module",
            "package",
            "import",
            "def ",
            "const ",
            "let ",
            "var ",
            "async ",
            "await ",
            "error",
            "bug",
            "exception",
            "traceback",
            "stack trace",
            "codebase",
            "repository",
            "repo",
            "project",
            "source",
            "pipeline",
            "service",
            "handler",
            "controller",
            "middleware",
            "api",
            "endpoint",
            "route",
            "model",
            "schema",
            "database",
        )
        has_code_context = has_code_file or any(
            kw in task_lower for kw in code_keywords
        )

        # Skip agents for non-code-only tasks without code context.
        if has_non_code_file and not has_code_file and not has_code_context:
            return WorkflowType.NONE
        if not has_code_context:
            return WorkflowType.NONE

        explore_kw = (
            "find",
            "search",
            "list",
            "show",
            "where",
            "how",
            "explore",
            "review",
            "look",
            "examine",
            "analyze",
            "explain",
            "understand",
            "describe",
            "read",
            "see",
            "check out",
        )
        edit_kw = (
            "fix",
            "add",
            "change",
            "update",
            "modify",
            "edit",
            "implement",
            "refactor",
            "rename",
            "remove",
            "delete",
            "create",
            "write",
            "replace",
            "insert",
            "move",
            "rewrite",
        )
        validate_kw = ("test", "verify", "validate", "run tests", "unittest", "check")

        # Priority 1: user intent.
        if any(kw in user_intent for kw in validate_kw):
            return WorkflowType.VALIDATE
        if any(kw in user_intent for kw in edit_kw):
            return WorkflowType.EDIT
        if any(kw in user_intent for kw in explore_kw):
            return WorkflowType.EXPLORE

        # Priority 2: full task (fallback).
        if any(kw in task_lower for kw in validate_kw):
            return WorkflowType.VALIDATE
        if any(kw in task_lower for kw in edit_kw):
            return WorkflowType.EDIT
        if any(kw in task_lower for kw in explore_kw):
            return WorkflowType.EXPLORE

        return WorkflowType.NONE

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

    def _synthesize_for_user(self, task: str, scout_result: AgentResult) -> str:
        """
        Synthesize Scout's findings into a human-readable response.

        WHY: Scout returns structured data; synthesis happens here only when
        the user is the audience (EXPLORE workflow). EDIT/FULL workflows pass
        raw context to downstream agents without this overhead.

        TRADEOFF: One LLM call for EXPLORE; but EDIT/FULL are faster.
        """
        if not scout_result.success:
            return f"Scout exploration failed: {scout_result.error or 'Unknown error'}"

        # Build context from Scout's structured findings
        files_found = scout_result.files_found or []
        symbols_found = scout_result.symbols_found or []
        metadata = scout_result.metadata or {}
        search_results = metadata.get("search_results", [])
        memory_context = metadata.get("memory_context", [])
        elapsed_ms = metadata.get("elapsed_ms", 0)

        # If no findings, return early
        if not files_found and not search_results:
            return f"No relevant files or code found for: {task}\n\n(Scout runtime: {elapsed_ms:.0f}ms)"

        # Build synthesis prompt
        context_parts = []

        if files_found:
            context_parts.append(f"**Files found ({len(files_found)}):**")
            for f in files_found[:20]:  # Cap display
                context_parts.append(f"  - {f}")
            if len(files_found) > 20:
                context_parts.append(f"  ... and {len(files_found) - 20} more")

        if symbols_found:
            context_parts.append(f"\n**Symbols found ({len(symbols_found)}):**")
            for s in symbols_found[:15]:
                context_parts.append(f"  - {s}")

        if search_results:
            context_parts.append(f"\n**Code snippets ({len(search_results)}):**")
            for r in search_results[:10]:
                context_parts.append(f"  {r['file']}:{r['line']}")
                content_preview = r.get("content", "")[:100].replace("\n", " ")
                if content_preview:
                    context_parts.append(f"    → {content_preview}")

        if memory_context:
            context_parts.append("\n**Historical context:**")
            for line in memory_context[:5]:
                context_parts.append(f"  {line}")

        findings_context = "\n".join(context_parts)

        # Use LLM to synthesize
        prompt = f"""Based on the codebase exploration below, provide a clear, helpful answer to the user's question.

**User's question:** {task}

**Exploration findings:**
{findings_context}

Provide a concise explanation that:
1. Directly answers the user's question
2. References specific files and code when relevant
3. Explains how the pieces fit together

Be helpful and specific. If the findings don't fully answer the question, say what was found and what might be missing."""

        try:
            # Use interpreter's LLM directly (bypass agent machinery)
            if self.interpreter and hasattr(self.interpreter, "llm"):
                messages = [{"role": "user", "type": "message", "content": prompt}]
                response_chunks = []
                for chunk in self.interpreter.llm.run(messages):
                    if chunk.get("type") == "message" and chunk.get("content"):
                        response_chunks.append(chunk["content"])
                synthesis = "".join(response_chunks).strip()
                if synthesis:
                    return f"{synthesis}\n\n---\n_Scout found {len(files_found)} file(s), {len(symbols_found)} symbol(s) in {elapsed_ms:.0f}ms_"
        except Exception as e:
            logger.debug(f"LLM synthesis failed: {e}")

        # Fallback: return structured findings without LLM synthesis
        return f"## Exploration Results\n\n{findings_context}\n\n---\n_Scout runtime: {elapsed_ms:.0f}ms_"

    def _run_explore_workflow(self, task: str, result: WorkflowResult) -> None:
        """Scout-only workflow. Synthesizes findings for user."""
        _scout_id, scout_result = self._execute_agent_with_events(AgentRole.SCOUT, task)
        result.agent_results[AgentRole.SCOUT] = scout_result
        if not scout_result.success:
            result.errors.append("Scout phase failed")
            return

        # Synthesize for user (EXPLORE is user-facing)
        synthesis = self._synthesize_for_user(task, scout_result)
        # Store synthesis in scout_result.content so respond.py can access it
        scout_result.content = synthesis

    def _run_edit_workflow(
        self, task: str, result: WorkflowResult, auto_apply: bool
    ) -> None:
        """Scout -> Surgeon workflow."""
        scout_id, scout_result = self._execute_agent_with_events(AgentRole.SCOUT, task)
        result.agent_results[AgentRole.SCOUT] = scout_result
        if not scout_result.success:
            result.errors.append("Scout phase failed")
            return

        _surgeon_id, surgeon_result = self._execute_agent_with_events(
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
            _validator_id, validator_result = self._execute_agent_with_events(
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
        _validator_id, validator_result = self._execute_agent_with_events(
            AgentRole.VALIDATOR, task
        )
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
