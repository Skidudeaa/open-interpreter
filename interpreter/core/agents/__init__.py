"""
Multi-Agent Orchestration for Open Interpreter.

Provides specialized agents for different coding tasks:
- ScoutAgent: Codebase exploration (fast, shallow reads)
- ArchitectAgent: Structural understanding (AST, dependency graphs)
- SurgeonAgent: Precise edits (minimal, correct changes)
- ValidatorAgent: Test & verify (execution, assertions)

The AgentOrchestrator coordinates these agents to handle
complex coding tasks more effectively than a single agent.
"""

# Unified types (single source of truth)
# Agent implementations
from .architect_agent import ArchitectAgent
from .base_agent import BaseAgent
from .orchestrator import AgentOrchestrator
from .research_agent import ResearchAgent, ResearchConfig
from .scout_agent import ScoutAgent
from .surgeon_agent import SurgeonAgent
from .types import (
    AgentConfig,
    AgentResult,
    AgentRole,
    AgentStatus,
    WorkflowResult,
    WorkflowStep,
)
from .validator_agent import ValidatorAgent

__all__ = [
    # Types
    "AgentRole",
    "AgentStatus",
    "AgentConfig",
    "AgentResult",
    "WorkflowStep",
    "WorkflowResult",
    "ResearchConfig",
    # Agents
    "BaseAgent",
    "AgentOrchestrator",
    "ScoutAgent",
    "SurgeonAgent",
    "ArchitectAgent",
    "ValidatorAgent",
    "ResearchAgent",
]
