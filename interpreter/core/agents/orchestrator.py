"""
AgentOrchestrator - Coordinates multiple specialized agents.

Routes tasks to appropriate agents and manages the workflow:
1. Scout: Find relevant files and code
2. Architect: Analyze structure (optional)
3. Surgeon: Make precise edits
4. Validator: Test the changes

The orchestrator determines which agents to use based on the task.
"""

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

from .base_agent import AgentResult, AgentRole, BaseAgent

# Import UI event system for agent visualization
HAS_UI_EVENTS = False
EventBus = None
EventType = None
UIEvent = None
get_event_bus = None
UIAgentRole = None
AgentStatus = None

try:
    from ...terminal_interface.components.ui_events import (
        EventBus,
        EventType,
        UIEvent,
        get_event_bus,
    )
    from ...terminal_interface.components.ui_state import AgentRole as UIAgentRole

    HAS_UI_EVENTS = True
except ImportError:
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


@dataclass
class WorkflowResult:
    """Result from a complete workflow."""

    workflow_type: WorkflowType
    success: bool
    agent_results: dict[AgentRole, AgentResult] = field(default_factory=dict)
    total_duration_ms: float = 0
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


class AgentOrchestrator:
    """
    Coordinates specialized agents to handle complex tasks.

    Usage:
        orchestrator = AgentOrchestrator(interpreter)
        result = orchestrator.handle_task("fix the login bug")
    """

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
            root_path: Root path for file operations
            event_bus: Optional EventBus for UI updates
        """
        self.interpreter = interpreter
        self.memory = memory or interpreter.semantic_graph
        self.root_path = root_path or os.getcwd()

        # Lazy-load agents
        self._agents: dict[AgentRole, BaseAgent] = {}

        # Event bus for UI updates
        self.event_bus = event_bus
        if self.event_bus is None and HAS_UI_EVENTS:
            self.event_bus = get_event_bus()

        # Track agent IDs for event emission
        self._agent_counter = 0

    def _emit_agent_event(
        self, event_type: "EventType", agent_id: str, role: AgentRole, **data
    ):
        """
        Emit an agent event to the UI.

        Args:
            event_type: The event type
            agent_id: The agent ID
            role: The agent role
            **data: Additional event data
        """
        if not HAS_UI_EVENTS or not self.event_bus:
            return

        # Convert core AgentRole to UI AgentRole
        ui_role = UIAgentRole.from_core_role(role)

        event_data = {"agent_id": agent_id, "role": ui_role.value, **data}

        event = UIEvent(type=event_type, data=event_data, source="orchestrator")
        self.event_bus.emit(event)

    def _generate_agent_id(self, role: AgentRole) -> str:
        """
        Generate a unique agent ID.

        Args:
            role: The agent role

        Returns:
            Unique agent ID
        """
        self._agent_counter += 1
        return f"{role.value}-{self._agent_counter}"

    def _execute_agent_with_events(
        self,
        role: AgentRole,
        task: str,
        context: str | None = None,
        parent_id: str | None = None,
    ) -> tuple[str, AgentResult]:
        """
        Execute an agent with event emission.

        Args:
            role: Agent role
            task: Task description
            context: Optional context from previous agent
            parent_id: Optional parent agent ID

        Returns:
            Tuple of (agent_id, agent_result)
        """
        # Generate agent ID and emit spawn event
        agent_id = self._generate_agent_id(role)
        self._emit_agent_event(
            EventType.AGENT_SPAWN if HAS_UI_EVENTS else None,
            agent_id,
            role,
            task=task,
            parent_id=parent_id,
        )

        # Execute agent
        agent = self.get_agent(role)
        try:
            agent_result = agent.execute(task, context=context)

            # Emit completion or error event
            if agent_result.success:
                self._emit_agent_event(
                    EventType.AGENT_COMPLETE if HAS_UI_EVENTS else None,
                    agent_id,
                    role,
                    result=str(agent_result.content)[:200],
                )
            else:
                self._emit_agent_event(
                    EventType.AGENT_ERROR if HAS_UI_EVENTS else None,
                    agent_id,
                    role,
                    error=f"{role.value} execution failed",
                )

            return agent_id, agent_result

        except Exception as e:
            self._emit_agent_event(
                EventType.AGENT_ERROR if HAS_UI_EVENTS else None,
                agent_id,
                role,
                error=str(e),
            )
            raise

    def get_agent(self, role: AgentRole) -> BaseAgent:
        """
        Get or create an agent by role.

        Args:
            role: The agent role

        Returns:
            The agent instance
        """
        if role not in self._agents:
            self._agents[role] = self._create_agent(role)
        return self._agents[role]

    def _create_agent(self, role: AgentRole) -> BaseAgent:
        """
        Create an agent for the given role.

        ARCHITECTURE: Agents receive orchestrator reference for inter-agent
        communication via ask_agent().

        WHY: Enables agents to collaborate - Scout can ask Architect about
        structure, Surgeon can ask Scout for related files.
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

        # Wire orchestrator reference for inter-agent communication
        agent._orchestrator = self

        return agent

    def handle_task(
        self,
        task: str,
        workflow: WorkflowType | None = None,
        auto_apply: bool = False,
    ) -> WorkflowResult:
        """
        Handle a task using the appropriate workflow.

        Args:
            task: The task description
            workflow: Workflow type (auto-detected if None)
            auto_apply: Automatically apply edits if True

        Returns:
            WorkflowResult with all agent results
        """
        start_time = time.time()

        # Determine workflow type if not specified
        if workflow is None:
            workflow = self._detect_workflow(task)

        result = WorkflowResult(workflow_type=workflow, success=True)

        try:
            if workflow == WorkflowType.EXPLORE:
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
            result.success = False
            result.errors.append(str(e))

        result.total_duration_ms = (time.time() - start_time) * 1000

        # Build final context from all results
        result.final_context = self._build_final_context(result)

        # Determine overall success
        if not result.errors:
            result.success = all(r.success for r in result.agent_results.values())

        return result

    def _detect_workflow(self, task: str) -> WorkflowType:
        """
        Detect the appropriate workflow from the task.

        WHY: Route tasks to specialized agents for better results.
        TRADEOFF: Simple keyword matching vs LLM-based classification.
                  Keywords are fast but can misroute; we prioritize user intent.
        """
        task_lower = task.lower()

        # Skip agent routing for short/simple messages
        if len(task) < 30:
            return WorkflowType.NONE

        # Extract user intent BEFORE @file expansion (everything before first @)
        # This is the user's actual request, not expanded file content
        if "@" in task:
            user_intent = task.split("@")[0].strip().lower()
        else:
            user_intent = task_lower[:100]  # First 100 chars for long messages

        # Strong intent words - if user says these, trust them and route to agents
        # No need for code indicators when intent is explicit
        strong_explore = {
            "review",
            "explain",
            "analyze",
            "examine",
            "look at",
            "check out",
            "walk through",
        }
        strong_edit = {"fix", "refactor", "rewrite", "implement", "add feature"}
        strong_validate = {"run tests", "test this", "verify"}

        if any(kw in user_intent for kw in strong_explore):
            return WorkflowType.EXPLORE
        if any(kw in user_intent for kw in strong_validate):
            return WorkflowType.VALIDATE
        if any(kw in user_intent for kw in strong_edit):
            return WorkflowType.EDIT

        # Code file extensions that warrant agent routing
        code_extensions = {
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".go",
            ".rs",
            ".rb",
            ".java",
            ".cpp",
            ".c",
            ".h",
            ".hpp",
            ".cs",
            ".swift",
            ".kt",
            ".php",
            ".scala",
            ".ex",
            ".exs",
            ".clj",
            ".hs",
            ".ml",
        }
        # Non-code extensions - skip agent routing
        non_code_extensions = {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".ico",
            ".mp3",
            ".mp4",
            ".wav",
            ".pdf",
            ".zip",
            ".tar",
        }

        # Check if task references code files (not images/media)
        has_code_file = any(ext in task_lower for ext in code_extensions)
        has_non_code_only = (
            any(ext in task_lower for ext in non_code_extensions) and not has_code_file
        )

        # Code context keywords (language-agnostic)
        code_keywords = {
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
            # Project/codebase references
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
        }
        has_code_context = has_code_file or any(
            kw in task_lower for kw in code_keywords
        )

        # Skip agents for non-code files without code context
        if has_non_code_only and not has_code_context:
            return WorkflowType.NONE
        if not has_code_context:
            return WorkflowType.NONE

        # Workflow keywords - check user intent first, then full task
        explore_kw = {
            "find",
            "search",
            "list",
            "show",
            "what",
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
        }
        edit_kw = {
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
        }
        validate_kw = {"test", "verify", "validate", "run tests", "unittest"}

        # Priority 1: User's explicit intent (before @file content)
        if user_intent:
            if any(kw in user_intent for kw in explore_kw):
                return WorkflowType.EXPLORE
            if any(kw in user_intent for kw in validate_kw):
                return WorkflowType.VALIDATE
            if any(kw in user_intent for kw in edit_kw):
                return WorkflowType.EDIT

        # Priority 2: Full task content (fallback)
        if any(kw in task_lower for kw in validate_kw):
            return WorkflowType.VALIDATE
        if any(kw in task_lower for kw in edit_kw):
            return WorkflowType.EDIT
        if any(kw in task_lower for kw in explore_kw):
            return WorkflowType.EXPLORE

        return WorkflowType.NONE

    def _run_explore_workflow(
        self,
        task: str,
        result: WorkflowResult,
    ):
        """Run exploration-only workflow."""
        scout_id, scout_result = self._execute_agent_with_events(AgentRole.SCOUT, task)
        result.agent_results[AgentRole.SCOUT] = scout_result

    def _run_edit_workflow(
        self,
        task: str,
        result: WorkflowResult,
        auto_apply: bool,
    ):
        """Run Scout -> Surgeon workflow."""
        # Scout phase
        scout_id, scout_result = self._execute_agent_with_events(AgentRole.SCOUT, task)
        result.agent_results[AgentRole.SCOUT] = scout_result

        if not scout_result.success:
            result.errors.append("Scout phase failed")
            return

        # Surgeon phase
        surgeon_id, surgeon_result = self._execute_agent_with_events(
            AgentRole.SURGEON,
            task,
            context=scout_result.context_for_next,
            parent_id=scout_id,
        )
        result.agent_results[AgentRole.SURGEON] = surgeon_result

        if surgeon_result.success and auto_apply:
            # Apply the edits
            surgeon = self.get_agent(AgentRole.SURGEON)
            for edit in surgeon.get_pending_edits():
                if not surgeon.apply_edit(edit):
                    result.errors.append(f"Failed to apply edit to {edit.file_path}")

    def _run_full_workflow(
        self,
        task: str,
        result: WorkflowResult,
        auto_apply: bool,
    ):
        """Run Scout -> Architect -> Surgeon -> Validator workflow."""
        # Scout phase
        scout_id, scout_result = self._execute_agent_with_events(AgentRole.SCOUT, task)
        result.agent_results[AgentRole.SCOUT] = scout_result

        if not scout_result.success:
            result.errors.append("Scout phase failed")
            return

        context = scout_result.context_for_next

        # Architect phase
        architect_id, architect_result = self._execute_agent_with_events(
            AgentRole.ARCHITECT, task, context=context, parent_id=scout_id
        )
        result.agent_results[AgentRole.ARCHITECT] = architect_result

        if not architect_result.success:
            result.errors.append("Architect phase failed")
            return

        context = architect_result.context_for_next or context
        parent_for_surgeon = architect_id

        # Surgeon phase
        surgeon_id, surgeon_result = self._execute_agent_with_events(
            AgentRole.SURGEON, task, context=context, parent_id=parent_for_surgeon
        )
        result.agent_results[AgentRole.SURGEON] = surgeon_result

        if not surgeon_result.success:
            result.errors.append("Surgeon phase failed")
            return

        # Apply edits if requested
        if auto_apply:
            surgeon = self.get_agent(AgentRole.SURGEON)
            for edit in surgeon.get_pending_edits():
                if not surgeon.apply_edit(edit):
                    result.errors.append(f"Failed to apply edit to {edit.file_path}")

        # Validator phase (run after edits are applied)
        if auto_apply:
            validator_id, validator_result = self._execute_agent_with_events(
                AgentRole.VALIDATOR,
                f"Validate edits for: {task}",
                context=surgeon_result.context_for_next,
                parent_id=surgeon_id,
            )
            result.agent_results[AgentRole.VALIDATOR] = validator_result

    def _run_validate_workflow(
        self,
        task: str,
        result: WorkflowResult,
    ):
        """Run validation-only workflow."""
        validator_id, validator_result = self._execute_agent_with_events(
            AgentRole.VALIDATOR, task
        )
        result.agent_results[AgentRole.VALIDATOR] = validator_result

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
