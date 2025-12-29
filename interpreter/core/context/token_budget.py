"""
Token Budget Calculator for context compaction.

# ARCHITECTURE: Provides accurate token counting for binary search optimization.
# WHY: Need to know exactly how many tokens remain for conversation history.
# TRADEOFF: tiktoken overhead vs. accurate budget enforcement.
"""

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core import OpenInterpreter

logger = logging.getLogger(__name__)


class TokenBudgetCalculator:
    """
    Calculate token budgets and counts for context compaction.

    Uses tiktoken for accurate per-model token counting and provides
    utilities for calculating available context window space.
    """

    def __init__(self, interpreter: "OpenInterpreter"):
        """
        Initialize the token budget calculator.

        Args:
            interpreter: The OpenInterpreter instance for model info
        """
        self.interpreter = interpreter
        self._encoding = None

    @property
    def encoding(self):
        """Lazy-load tiktoken encoder for the current model."""
        if self._encoding is None:
            try:
                import tiktoken

                model = self.interpreter.llm.model or "gpt-4"
                # Strip provider prefix if present (e.g., "openai/gpt-4" -> "gpt-4")
                if "/" in model:
                    model = model.split("/")[-1]

                try:
                    self._encoding = tiktoken.encoding_for_model(model)
                except KeyError:
                    # Fall back to cl100k_base for unknown models
                    self._encoding = tiktoken.get_encoding("cl100k_base")
            except ImportError:
                logger.warning(
                    "tiktoken not available, using character-based estimation"
                )
                self._encoding = None

        return self._encoding

    def get_context_window(self) -> int:
        """Get the model's context window size."""
        if self.interpreter.llm.context_window:
            return self.interpreter.llm.context_window

        # Try to detect from litellm
        try:
            import litellm

            model = self.interpreter.llm.model
            info = litellm.get_model_info(model)
            return info.get("max_input_tokens", 8000)
        except Exception:
            return 8000  # Conservative default

    def get_max_tokens(self) -> int:
        """Get the maximum output tokens."""
        if self.interpreter.llm.max_tokens:
            return self.interpreter.llm.max_tokens

        # Default to 20% of context window
        return int(self.get_context_window() * 0.2)

    def get_available_tokens(self, system_message: str) -> int:
        """
        Calculate tokens available for conversation history.

        Args:
            system_message: The system message content

        Returns:
            Available token budget for messages
        """
        context_window = self.get_context_window()
        max_tokens = self.get_max_tokens()

        system_tokens = self.count_text(system_message)
        buffer = 100  # Safety buffer for formatting overhead

        available = context_window - max_tokens - system_tokens - buffer
        return max(available, 500)  # Ensure at least some space

    def count_text(self, text: str) -> int:
        """
        Count tokens in text.

        Args:
            text: The text to count

        Returns:
            Token count
        """
        if not text:
            return 0

        if self.encoding:
            return len(self.encoding.encode(text))
        else:
            # Fallback: rough estimate of 4 chars per token
            return len(text) // 4

    def count_message(self, message: dict[str, Any]) -> int:
        """
        Count tokens in a single message.

        Args:
            message: Message dict with role and content

        Returns:
            Token count including message overhead
        """
        content = message.get("content", "")

        if isinstance(content, str):
            tokens = self.count_text(content)
        elif isinstance(content, list):
            # Handle multimodal content
            tokens = 0
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        tokens += self.count_text(item.get("text", ""))
                    elif item.get("type") == "image_url":
                        tokens += 85  # Base image token cost
                elif isinstance(item, str):
                    tokens += self.count_text(item)
        else:
            tokens = 0

        # Add overhead for message structure (role, delimiters)
        return tokens + 4

    def count_messages(self, messages: list[dict[str, Any]]) -> int:
        """
        Count total tokens in a message list.

        Args:
            messages: List of message dicts

        Returns:
            Total token count
        """
        total = 0
        for msg in messages:
            total += self.count_message(msg)

        # Add conversation overhead
        total += 3  # Priming tokens

        return total

    def estimate_summary_tokens(self, message_count: int) -> int:
        """
        Estimate tokens a technical flow summary will use.

        # WHY: Need to predict summary size for binary search optimization.
        # TRADEOFF: Estimate accuracy vs. computation overhead.

        Args:
            message_count: Number of messages being summarized

        Returns:
            Estimated token count for the summary
        """
        # Heuristic based on observation:
        # - Base overhead: ~100 tokens for headers/structure
        # - Per message: ~10-20 tokens depending on complexity
        # - Code-heavy conversations: higher token count

        base = 100
        per_message = 15
        max_summary = 800  # Cap summary size

        estimated = base + (message_count * per_message)
        return min(estimated, max_summary)
