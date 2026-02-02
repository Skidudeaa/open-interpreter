"""
Technical Flow Generator for context compaction.

# ARCHITECTURE: LLM-based generation of technical flow documents with decision points and diffs.
# WHY: Preserve context better than summaries by capturing WHY decisions were made.
# TRADEOFF: LLM call latency vs. high-quality context preservation.
# NOTE: Falls back to extraction-only if LLM call fails.
"""

import difflib
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core import OpenInterpreter

logger = logging.getLogger(__name__)


# Prompt for generating technical flow documents
FLOW_PROMPT = """Generate a concise technical flow document for this conversation segment.

## FORMAT REQUIREMENTS:
1. Group into phases by user goal/task
2. For EACH decision point: state the choice made and WHY
3. Include abbreviated inline diffs for file changes (max 5 lines each)
4. End with "Current State" checkpoint

## OUTPUT FORMAT:
```
## Technical Flow [messages summarized]

### Phase: [Goal Name]
- User goal: [what they wanted]
- Decision: [choice A vs choice B] → chose [X]
  WHY: [reasoning]

### Phase: [Implementation]
- Modified `filename.py`:
  ```diff
  +added line
  -removed line
  ```
- Decision: [choice] → chose [X]
  WHY: [reasoning]

### Current State
- [completed items]
- Pending: [remaining work]
```

## CRITICAL RULES:
- Do NOT write a summary. Write a TECHNICAL CHANGELOG.
- Include decision points with WHY for each significant choice
- Keep diffs abbreviated (3-5 lines max per file)
- Be concise but preserve technical accuracy

## CONVERSATION TO PROCESS:
{conversation}"""


class TechnicalFlowGenerator:
    """
    Generates technical flow documents from conversation history.

    Unlike summaries, technical flows capture:
    - Decision points with WHY reasoning
    - Abbreviated diffs of file changes
    - Phase-based organization of work
    - Current state checkpoints
    """

    def __init__(self, interpreter: "OpenInterpreter"):
        """
        Initialize the flow generator.

        Args:
            interpreter: The OpenInterpreter instance for LLM access
        """
        self.interpreter = interpreter

    def generate(self, messages: list[dict[str, Any]]) -> str:
        """
        Generate a technical flow document from messages.

        Args:
            messages: List of message dicts to summarize

        Returns:
            Technical flow markdown document
        """
        # First, extract diffs from code/console messages
        diffs = self._extract_diffs(messages)

        # Format messages for the LLM
        formatted = self._format_for_flow(messages, diffs)

        # Call LLM to generate flow document
        try:
            flow = self._call_flow_llm(formatted)
            if flow:
                return flow
        except Exception as e:
            logger.debug(f"LLM flow generation failed: {e}")

        # Fallback to extraction-based flow
        return self._fallback_flow(messages, diffs)

    def _extract_diffs(self, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        """
        Extract file diffs from code execution messages.

        Looks for file write patterns and creates abbreviated diffs.
        """
        diffs = []

        for msg in messages:
            msg_type = msg.get("type", "")
            content = msg.get("content", "")

            if not isinstance(content, str):
                continue

            # Look for file write patterns in code
            if msg_type == "code":
                # Match common file write patterns
                file_patterns = [
                    r'open\(["\']([^"\']+)["\'].*?write',
                    r'Path\(["\']([^"\']+)["\'].*?write',
                    r'with open\(["\']([^"\']+)["\']',
                    r'\.write_text\(["\']([^"\']+)["\']',
                ]

                for pattern in file_patterns:
                    matches = re.findall(pattern, content)
                    for match in matches:
                        diffs.append(
                            {
                                "file": match,
                                "action": "modified",
                                "context": (
                                    content[:200] + "..."
                                    if len(content) > 200
                                    else content
                                ),
                            }
                        )

            # Look for error patterns in console output
            elif msg_type == "console":
                if "error" in content.lower() or "exception" in content.lower():
                    # Extract first few lines of error
                    error_lines = content.split("\n")[:5]
                    diffs.append(
                        {
                            "file": "console",
                            "action": "error",
                            "context": "\n".join(error_lines),
                        }
                    )

        return diffs

    def _format_for_flow(
        self, messages: list[dict[str, Any]], diffs: list[dict[str, str]]
    ) -> str:
        """
        Format messages into readable text for LLM processing.

        Args:
            messages: List of messages to format
            diffs: Extracted diff information

        Returns:
            Formatted text for LLM prompt
        """
        parts = []

        for msg in messages:
            role = msg.get("role", "unknown")
            msg_type = msg.get("type", "message")
            content = msg.get("content", "")

            if not isinstance(content, str):
                content = str(content)

            if msg_type == "code":
                lang = msg.get("format", "code")
                # Truncate long code
                if len(content) > 300:
                    content = content[:300] + "\n... [truncated]"
                parts.append(f"[{role}] Executed {lang}:\n```\n{content}\n```")

            elif msg_type == "console":
                # Truncate long outputs
                if len(content) > 200:
                    content = content[:200] + "\n... [output truncated]"
                parts.append(f"[output]\n{content}")

            else:
                # Regular message
                if len(content) > 500:
                    content = content[:500] + "... [truncated]"
                parts.append(f"[{role}] {content}")

        # Append diff summary if we found any
        if diffs:
            parts.append("\n--- FILE CHANGES DETECTED ---")
            for diff in diffs[:5]:  # Limit to 5 diffs
                parts.append(f"- {diff['action']}: {diff['file']}")

        return "\n\n".join(parts)

    def _call_flow_llm(self, formatted_conversation: str) -> str | None:
        """
        Call LLM to generate technical flow document.

        Uses a smaller/faster model if available for efficiency.
        """
        prompt = FLOW_PROMPT.format(conversation=formatted_conversation)

        # Use the interpreter's LLM
        messages = [{"role": "user", "content": prompt}]

        try:
            response_text = ""

            # Use litellm directly for a simpler call
            import litellm

            model = self.interpreter.llm.model or "gpt-4o-mini"

            # Prefer a faster model for summarization if using expensive model
            if "opus" in model.lower() or "sonnet" in model.lower():
                # Use a faster model for flow generation
                summary_model = model.replace("opus", "haiku").replace(
                    "sonnet", "haiku"
                )
                if summary_model == model:
                    summary_model = "gpt-4o-mini"
            elif "gpt-4o" in model.lower() and "mini" not in model.lower():
                summary_model = "gpt-4o-mini"
            else:
                summary_model = model

            response = litellm.completion(
                model=summary_model,
                messages=messages,
                max_tokens=1000,
                temperature=0.3,
            )

            if response.choices:
                response_text = response.choices[0].message.content

            return response_text if response_text else None

        except Exception as e:
            logger.debug(f"Flow LLM call failed: {e}")
            return None

    def _fallback_flow(
        self, messages: list[dict[str, Any]], diffs: list[dict[str, str]]
    ) -> str:
        """
        Generate a basic flow document without LLM.

        Used as fallback when LLM call fails.
        """
        parts = ["## Technical Flow [extraction-based fallback]"]

        # Extract user goals
        user_goals = []
        for msg in messages:
            if msg.get("role") == "user" and msg.get("type") == "message":
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    user_goals.append(content[:100])

        if user_goals:
            parts.append("\n### User Goals")
            for i, goal in enumerate(user_goals[:3], 1):
                parts.append(f"- Goal {i}: {goal}...")

        # List actions taken
        actions = []
        for msg in messages:
            if msg.get("type") == "code":
                lang = msg.get("format", "code")
                actions.append(f"Executed {lang} code")
            elif (
                msg.get("type") == "console"
                and "error" in msg.get("content", "").lower()
            ):
                actions.append("Encountered error")

        if actions:
            parts.append("\n### Actions Taken")
            for action in actions[:5]:
                parts.append(f"- {action}")

        # List file changes
        if diffs:
            parts.append("\n### File Changes")
            for diff in diffs[:5]:
                parts.append(f"- {diff['action']}: `{diff['file']}`")

        parts.append("\n### Current State")
        parts.append(f"- Processed {len(messages)} messages")
        parts.append(f"- {len(diffs)} file modifications detected")

        return "\n".join(parts)

    def create_diff_block(
        self, original: str, modified: str, filename: str, max_lines: int = 5
    ) -> str:
        """
        Create an abbreviated diff block for a file change.

        Args:
            original: Original file content
            modified: Modified file content
            filename: Name of the file
            max_lines: Maximum diff lines to include

        Returns:
            Formatted diff block
        """
        if not original and not modified:
            return ""

        orig_lines = original.split("\n") if original else []
        mod_lines = modified.split("\n") if modified else []

        diff = list(
            difflib.unified_diff(
                orig_lines,
                mod_lines,
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
                lineterm="",
            )
        )

        if not diff:
            return ""

        # Take only the first max_lines of actual changes
        change_lines = [
            line for line in diff if line.startswith("+") or line.startswith("-")
        ]
        change_lines = change_lines[:max_lines]

        if len(diff) > max_lines + 4:  # Header is ~4 lines
            change_lines.append(f"... [{len(diff) - max_lines - 4} more lines]")

        return "```diff\n" + "\n".join(change_lines) + "\n```"
