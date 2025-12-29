"""
Context Management for Open Interpreter.

Provides intelligent context compaction using LLM-generated technical flows
instead of simple message deletion. Key components:

- ContextCompactor: Main orchestrator with binary search for optimal split
- TechnicalFlowGenerator: LLM-based flow document generation with diffs
- TokenBudgetCalculator: Token counting and budget calculation
"""

from .compaction import ContextCompactor
from .flow_generator import TechnicalFlowGenerator
from .token_budget import TokenBudgetCalculator

__all__ = [
    "ContextCompactor",
    "TechnicalFlowGenerator",
    "TokenBudgetCalculator",
]
