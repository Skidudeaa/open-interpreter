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
from typing import Dict, List, Optional, Type, TYPE_CHECKING
from enum import Enum

from .base_agent import BaseAgent, AgentRole, AgentResult

if TYPE_CHECKING:
    from ..core import OpenInterpreter
    from ..memory import SemanticEditGraph


class WorkflowType(Enum):
    """Pre-defined workflow types."""
    EXPLORE = "explore"     # Scout only
    EDIT = "edit"           # Scout -> Surgeon
    FULL = "full"           # Scout -> Architect -> Surgeon -> Validator
    VALIDATE = "validate"   # Validator only


@dataclass
class WorkflowResult:
    """Result from a complete workflow."""
    workflow_type: WorkflowType
    success: bool
    agent_results: Dict[AgentRole, AgentResult] = field(default_factory=dict)
    total_duration_ms: float = 0
    final_context: str = ""
    errors: List[str] = field(default_factory=list)

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
        root_path: Optional[str] = None,
    ):
        """
        Initialize the orchestrator.

        Args:
            interpreter: The OpenInterpreter instance
            memory: Optional shared semantic memory
            root_path: Root path for file operations
        """
        self.interpreter = interpreter
        self.memory = memory or interpreter.semantic_graph
        self.root_path = root_path or os.getcwd()

        # Lazy-load agents
        self._agents: Dict[AgentRole, BaseAgent] = {}

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
        """Create an agent for the given role."""
        from .scout_agent import ScoutAgent
        from .surgeon_agent import SurgeonAgent

        agent_classes = {
            AgentRole.SCOUT: ScoutAgent,
            AgentRole.SURGEON: SurgeonAgent,
            # Add more as implemented
        }

        agent_class = agent_classes.get(role)
        if not agent_class:
            raise ValueError(f"No agent implementation for role: {role}")

        return agent_class(
            interpreter=self.interpreter,
            memory=self.memory,
            root_path=self.root_path,
        )

    def handle_task(
        self,
        task: str,
        workflow: Optional[WorkflowType] = None,
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
            result.success = all(
                r.success for r in result.agent_results.values()
            )

        return result

    def _detect_workflow(self, task: str) -> WorkflowType:
        """
        Detect the appropriate workflow from the task.

        Args:
            task: The task description

        Returns:
            WorkflowType
        """
        task_lower = task.lower()

        # Keywords for different workflows
        explore_keywords = ['find', 'search', 'list', 'show', 'what', 'where', 'explore']
        edit_keywords = ['fix', 'add', 'change', 'update', 'modify', 'edit', 'implement']
        validate_keywords = ['test', 'check', 'verify', 'validate']

        # Check for keywords
        if any(kw in task_lower for kw in validate_keywords):
            return WorkflowType.VALIDATE

        if any(kw in task_lower for kw in edit_keywords):
            return WorkflowType.EDIT

        if any(kw in task_lower for kw in explore_keywords):
            return WorkflowType.EXPLORE

        # Default to edit for most tasks
        return WorkflowType.EDIT

    def _run_explore_workflow(
        self,
        task: str,
        result: WorkflowResult,
    ):
        """Run exploration-only workflow."""
        scout = self.get_agent(AgentRole.SCOUT)
        scout_result = scout.execute(task)
        result.agent_results[AgentRole.SCOUT] = scout_result

    def _run_edit_workflow(
        self,
        task: str,
        result: WorkflowResult,
        auto_apply: bool,
    ):
        """Run Scout -> Surgeon workflow."""
        # Scout phase
        scout = self.get_agent(AgentRole.SCOUT)
        scout_result = scout.execute(task)
        result.agent_results[AgentRole.SCOUT] = scout_result

        if not scout_result.success:
            result.errors.append("Scout phase failed")
            return

        # Surgeon phase
        surgeon = self.get_agent(AgentRole.SURGEON)
        surgeon_result = surgeon.execute(
            task,
            context=scout_result.context_for_next
        )
        result.agent_results[AgentRole.SURGEON] = surgeon_result

        if surgeon_result.success and auto_apply:
            # Apply the edits
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
        scout = self.get_agent(AgentRole.SCOUT)
        scout_result = scout.execute(task)
        result.agent_results[AgentRole.SCOUT] = scout_result

        if not scout_result.success:
            result.errors.append("Scout phase failed")
            return

        context = scout_result.context_for_next

        # Architect phase (if implemented)
        if AgentRole.ARCHITECT in self._agents or False:  # Check if available
            architect = self.get_agent(AgentRole.ARCHITECT)
            architect_result = architect.execute(task, context=context)
            result.agent_results[AgentRole.ARCHITECT] = architect_result
            context = architect_result.context_for_next or context

        # Surgeon phase
        surgeon = self.get_agent(AgentRole.SURGEON)
        surgeon_result = surgeon.execute(task, context=context)
        result.agent_results[AgentRole.SURGEON] = surgeon_result

        if not surgeon_result.success:
            result.errors.append("Surgeon phase failed")
            return

        # Apply edits if requested
        if auto_apply:
            for edit in surgeon.get_pending_edits():
                if not surgeon.apply_edit(edit):
                    result.errors.append(f"Failed to apply edit to {edit.file_path}")

        # Validator phase (if implemented and edits were applied)
        if auto_apply and AgentRole.VALIDATOR in self._agents or False:
            validator = self.get_agent(AgentRole.VALIDATOR)
            validator_result = validator.execute(
                f"Validate edits for: {task}",
                context=surgeon_result.context_for_next
            )
            result.agent_results[AgentRole.VALIDATOR] = validator_result

    def _run_validate_workflow(
        self,
        task: str,
        result: WorkflowResult,
    ):
        """Run validation-only workflow."""
        # For now, just run basic validation
        # Full validator agent can be implemented later
        result.errors.append("Validator agent not yet implemented")

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
def orchestrate(
    interpreter: "OpenInterpreter",
    task: str,
    **kwargs
) -> WorkflowResult:
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
