"""
ConversationLinker - Links code edits to conversation context.

This module extracts relevant context from the conversation that led to
an edit, enabling the Semantic Edit Graph to track WHY changes were made.

Key functions:
- Extract user intent from conversation
- Create ConversationContext objects
- Generate intent summaries
- Track conversation flow across edits
"""

import hashlib
import re
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple

from .edit_record import ConversationContext, Edit, EditType


class ConversationLinker:
    """
    Links code edits to their originating conversation context.
    """

    def __init__(self, interpreter=None):
        """
        Initialize the conversation linker.

        Args:
            interpreter: Optional OpenInterpreter instance for access to messages
        """
        self.interpreter = interpreter
        self._conversation_id = None

    def get_conversation_id(self) -> str:
        """
        Get or create a unique ID for the current conversation.
        """
        if self._conversation_id is None:
            # Generate based on timestamp and first message hash
            timestamp = datetime.now().isoformat()
            hash_input = timestamp
            if self.interpreter and self.interpreter.messages:
                first_msg = self.interpreter.messages[0].get("content", "")
                hash_input += first_msg[:100]
            self._conversation_id = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
        return self._conversation_id

    def set_conversation_id(self, conversation_id: str):
        """Set a specific conversation ID."""
        self._conversation_id = conversation_id

    def get_current_turn_index(self) -> int:
        """Get the current turn index in the conversation."""
        if self.interpreter and self.interpreter.messages:
            # Count user messages as turns
            return sum(1 for m in self.interpreter.messages if m.get("role") == "user")
        return 0

    def create_context(
        self,
        user_message: str,
        assistant_response: Optional[str] = None,
        intent_summary: Optional[str] = None,
    ) -> ConversationContext:
        """
        Create a ConversationContext for the current conversation state.

        Args:
            user_message: The user's message that prompted the edit
            assistant_response: The assistant's response (optional)
            intent_summary: Summary of the user's intent (optional)

        Returns:
            ConversationContext object
        """
        return ConversationContext(
            conversation_id=self.get_conversation_id(),
            turn_index=self.get_current_turn_index(),
            user_message=user_message,
            assistant_response=assistant_response,
            intent_summary=intent_summary or self.extract_intent(user_message),
        )

    def create_context_from_messages(
        self,
        messages: List[Dict[str, Any]],
        target_index: int = -1
    ) -> ConversationContext:
        """
        Create context from a message list.

        Args:
            messages: List of message dictionaries
            target_index: Index of the target message (default: last)

        Returns:
            ConversationContext object
        """
        if not messages:
            return ConversationContext(
                conversation_id=self.get_conversation_id(),
                turn_index=0,
                user_message="",
            )

        # Find the relevant user message
        user_messages = [m for m in messages if m.get("role") == "user"]

        if target_index < 0:
            target_index = len(user_messages) + target_index

        if 0 <= target_index < len(user_messages):
            user_msg = user_messages[target_index]
            user_content = user_msg.get("content", "")
        else:
            user_content = ""

        # Find the corresponding assistant response
        assistant_response = None
        for i, msg in enumerate(messages):
            if msg == user_msg:
                # Look for next assistant message
                for j in range(i + 1, len(messages)):
                    if messages[j].get("role") == "assistant":
                        assistant_response = messages[j].get("content", "")
                        break
                break

        return ConversationContext(
            conversation_id=self.get_conversation_id(),
            turn_index=target_index,
            user_message=user_content,
            assistant_response=assistant_response,
            intent_summary=self.extract_intent(user_content),
        )

    def extract_intent(self, user_message: str) -> str:
        """
        Extract a summary of user intent from a message.

        This is a simple heuristic approach. For better results,
        use LLM-based intent extraction.

        Args:
            user_message: The user's message

        Returns:
            A brief summary of the intent
        """
        if not user_message:
            return "Unknown intent"

        # Normalize and truncate
        message = user_message.strip().lower()

        # Common patterns
        patterns = [
            (r'^(fix|debug|solve|repair)\b', 'Bug fix'),
            (r'^(add|implement|create|build|make)\b', 'New feature'),
            (r'^(refactor|clean|reorganize|restructure)\b', 'Refactoring'),
            (r'^(optimize|improve|speed up|make faster)\b', 'Optimization'),
            (r'^(update|change|modify|edit)\b', 'Modification'),
            (r'^(remove|delete|drop)\b', 'Removal'),
            (r'^(test|write tests)\b', 'Testing'),
            (r'^(document|add docs|comment)\b', 'Documentation'),
            (r'\?$', 'Question/Exploration'),
        ]

        for pattern, intent_type in patterns:
            if re.search(pattern, message):
                # Extract the first sentence or phrase
                first_sentence = re.split(r'[.!?\n]', user_message)[0].strip()
                if len(first_sentence) > 80:
                    first_sentence = first_sentence[:77] + "..."
                return f"{intent_type}: {first_sentence}"

        # Default: first sentence
        first_sentence = re.split(r'[.!?\n]', user_message)[0].strip()
        if len(first_sentence) > 80:
            first_sentence = first_sentence[:77] + "..."
        return first_sentence

    def infer_edit_type(self, user_message: str) -> EditType:
        """
        Infer the edit type from a user message.

        Args:
            user_message: The user's message

        Returns:
            EditType enum value
        """
        if not user_message:
            return EditType.UNKNOWN

        message = user_message.strip().lower()

        # Pattern matching for edit types
        type_patterns = [
            (r'\b(fix|bug|error|issue|problem|crash|broken)\b', EditType.BUG_FIX),
            (r'\b(add|implement|create|build|new|feature)\b', EditType.FEATURE),
            (r'\b(refactor|clean|reorganize|restructure|simplify)\b', EditType.REFACTOR),
            (r'\b(optimize|performance|speed|faster|efficient)\b', EditType.OPTIMIZATION),
            (r'\b(test|tests|testing|spec|unittest)\b', EditType.TEST),
            (r'\b(doc|document|comment|readme|docstring)\b', EditType.DOCUMENTATION),
            (r'\b(depend|package|install|import|require)\b', EditType.DEPENDENCY),
            (r'\b(config|setting|environment|env)\b', EditType.CONFIGURATION),
        ]

        for pattern, edit_type in type_patterns:
            if re.search(pattern, message):
                return edit_type

        return EditType.UNKNOWN

    def get_recent_context(
        self,
        n_turns: int = 3
    ) -> List[ConversationContext]:
        """
        Get context from recent conversation turns.

        Args:
            n_turns: Number of recent turns to include

        Returns:
            List of ConversationContext objects
        """
        if not self.interpreter or not self.interpreter.messages:
            return []

        contexts = []
        user_messages = [
            (i, m) for i, m in enumerate(self.interpreter.messages)
            if m.get("role") == "user"
        ]

        for turn_idx, (msg_idx, msg) in enumerate(user_messages[-n_turns:]):
            user_content = msg.get("content", "")

            # Find assistant response
            assistant_response = None
            for j in range(msg_idx + 1, len(self.interpreter.messages)):
                if self.interpreter.messages[j].get("role") == "assistant":
                    assistant_response = self.interpreter.messages[j].get("content", "")
                    break

            contexts.append(ConversationContext(
                conversation_id=self.get_conversation_id(),
                turn_index=turn_idx,
                user_message=user_content,
                assistant_response=assistant_response,
                intent_summary=self.extract_intent(user_content),
            ))

        return contexts

    def link_edit_to_conversation(
        self,
        edit: Edit,
        messages: Optional[List[Dict[str, Any]]] = None
    ) -> Edit:
        """
        Add conversation context to an edit.

        Args:
            edit: The edit to link
            messages: Optional message list (uses interpreter.messages if None)

        Returns:
            The edit with conversation_context set
        """
        if messages is None and self.interpreter:
            messages = self.interpreter.messages

        if messages:
            edit.conversation_context = self.create_context_from_messages(messages)

        if not edit.edit_type or edit.edit_type == EditType.UNKNOWN:
            if edit.conversation_context:
                edit.edit_type = self.infer_edit_type(
                    edit.conversation_context.user_message
                )

        return edit


def create_edit_from_file_change(
    file_path: str,
    original_content: str,
    new_content: str,
    user_message: str,
    conversation_id: Optional[str] = None,
) -> Edit:
    """
    Convenience function to create an Edit with full context.

    Args:
        file_path: Path to the edited file
        original_content: Original file content
        new_content: New file content
        user_message: The user's message that prompted the edit
        conversation_id: Optional conversation ID

    Returns:
        Edit object with context
    """
    from .symbol_extractor import extract_affected_symbols
    import difflib

    # Extract affected symbols
    primary_symbol, affected_symbols = extract_affected_symbols(
        original_content, new_content, file_path
    )

    # Generate diff
    diff = "\n".join(difflib.unified_diff(
        original_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
    ))

    # Create linker for context
    linker = ConversationLinker()
    if conversation_id:
        linker.set_conversation_id(conversation_id)

    context = linker.create_context(user_message)
    edit_type = linker.infer_edit_type(user_message)

    return Edit(
        file_path=file_path,
        original_content=original_content,
        new_content=new_content,
        diff=diff,
        edit_type=edit_type,
        primary_symbol=primary_symbol,
        affected_symbols=affected_symbols,
        conversation_context=context,
        user_intent=context.intent_summary,
    )
