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

from .base_agent import BaseAgent, AgentRole, AgentResult
from .orchestrator import AgentOrchestrator
from .scout_agent import ScoutAgent
from .surgeon_agent import SurgeonAgent

__all__ = [
    "BaseAgent",
    "AgentRole",
    "AgentResult",
    "AgentOrchestrator",
    "ScoutAgent",
    "SurgeonAgent",
]
