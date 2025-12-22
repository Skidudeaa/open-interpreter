"""
Completers for Terminal Input

Provides auto-completion for:
- Magic commands (%help, %debug, etc.)
- File paths
- Conversation history
- Python keywords (optional)

Part of Phase 1: prompt_toolkit Integration

Usage:
    completer = create_completer(interpreter, history)
    session = PromptSession(completer=completer)
"""

from typing import TYPE_CHECKING, Optional, List, Iterable, Dict
import os
from pathlib import Path

from prompt_toolkit.completion import (
    Completer,
    Completion,
    FuzzyCompleter,
    FuzzyWordCompleter,
    PathCompleter,
    WordCompleter,
    merge_completers,
)
from prompt_toolkit.document import Document

if TYPE_CHECKING:
    from ...core.core import OpenInterpreter


# Magic commands with descriptions
MAGIC_COMMANDS: Dict[str, str] = {
    "%help": "Show available commands",
    "%debug": "Toggle debug mode",
    "%reset": "Reset conversation",
    "%save": "Save conversation to file",
    "%load": "Load conversation from file",
    "%undo": "Undo last action",
    "%model": "Show or change current model",
    "%system": "Show or set system message",
    "%tokens": "Show token usage",
    "%context": "Show context window status",
    "%history": "Show conversation history",
    "%clear": "Clear screen",
    "%zen": "Switch to zen mode (minimal UI)",
    "%power": "Switch to power mode (full UI)",
}


class MagicCommandCompleter(Completer):
    """
    Completer for magic commands (% prefix).

    Provides fuzzy matching for command names with descriptions.
    """

    def __init__(self, commands: Optional[Dict[str, str]] = None):
        self.commands = commands or MAGIC_COMMANDS

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        text = document.text_before_cursor
        word = document.get_word_before_cursor()

        # Only complete if starts with %
        if not text.lstrip().startswith('%'):
            return

        # Get the partial command
        partial = text.lstrip()

        for cmd, description in self.commands.items():
            if cmd.startswith(partial) or partial in cmd:
                # Calculate display text
                display = f"{cmd} - {description}"
                yield Completion(
                    text=cmd,
                    start_position=-len(partial),
                    display=display,
                    display_meta=description,
                )


class ConversationCompleter(Completer):
    """
    Completer that suggests from conversation history.

    Extracts unique phrases, file paths, and identifiers from
    previous messages for quick re-use.
    """

    def __init__(self, interpreter: "OpenInterpreter"):
        self.interpreter = interpreter
        self._cache: List[str] = []
        self._cache_size = 0

    def _update_cache(self) -> None:
        """Update completion cache from conversation"""
        messages = getattr(self.interpreter, 'messages', [])
        if len(messages) == self._cache_size:
            return

        self._cache_size = len(messages)
        suggestions = set()

        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, str):
                # Extract words (identifiers, paths, etc.)
                words = content.split()
                for word in words:
                    # Skip very short or very long words
                    if 3 <= len(word) <= 50:
                        # Clean up punctuation
                        clean = word.strip('.,!?()[]{}:;"\'')
                        if clean and not clean.isdigit():
                            suggestions.add(clean)

        self._cache = sorted(suggestions)

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        self._update_cache()

        word = document.get_word_before_cursor()
        if len(word) < 2:
            return

        word_lower = word.lower()
        for suggestion in self._cache:
            if suggestion.lower().startswith(word_lower):
                yield Completion(
                    text=suggestion,
                    start_position=-len(word),
                )


class FilePathCompleter(Completer):
    """
    Smart file path completer.

    Activates when:
    - Text contains path separators
    - Text starts with ~, /, or ./
    - After common path keywords (open, read, write, etc.)
    """

    PATH_KEYWORDS = {'open', 'read', 'write', 'load', 'save', 'file', 'path', 'from', 'to'}

    def __init__(self):
        self._path_completer = PathCompleter(
            expanduser=True,
            only_directories=False,
        )

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        text = document.text_before_cursor
        word = document.get_word_before_cursor()

        # Check if we should activate path completion
        should_complete = False

        # Starts with path indicator
        if word.startswith(('/', '~', './')):
            should_complete = True

        # Contains path separator
        if os.sep in word or '/' in word:
            should_complete = True

        # After path keyword
        words = text.lower().split()
        if words and any(kw in words for kw in self.PATH_KEYWORDS):
            should_complete = True

        if should_complete:
            yield from self._path_completer.get_completions(document, complete_event)


class CombinedCompleter(Completer):
    """
    Combines multiple completers with priority ordering.

    Order:
    1. Magic commands (if starts with %)
    2. File paths (if contains path chars)
    3. Conversation history
    """

    def __init__(self, interpreter: "OpenInterpreter"):
        self.magic_completer = MagicCommandCompleter()
        self.path_completer = FilePathCompleter()
        self.conversation_completer = ConversationCompleter(interpreter)

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        text = document.text_before_cursor.lstrip()

        # Magic commands first
        if text.startswith('%'):
            yield from self.magic_completer.get_completions(document, complete_event)
            return

        # File paths
        path_completions = list(self.path_completer.get_completions(document, complete_event))
        if path_completions:
            yield from path_completions
            return

        # Conversation history
        yield from self.conversation_completer.get_completions(document, complete_event)


def create_completer(
    interpreter: "OpenInterpreter",
    include_paths: bool = True,
    include_magic: bool = True,
    include_history: bool = True,
    fuzzy: bool = True,
) -> Completer:
    """
    Create a completer for the interpreter input.

    Args:
        interpreter: The OpenInterpreter instance
        include_paths: Include file path completion
        include_magic: Include magic command completion
        include_history: Include conversation history completion
        fuzzy: Use fuzzy matching

    Returns:
        Configured Completer instance
    """
    completers = []

    if include_magic:
        completers.append(MagicCommandCompleter())

    if include_paths:
        completers.append(FilePathCompleter())

    if include_history:
        completers.append(ConversationCompleter(interpreter))

    if not completers:
        return WordCompleter([])

    combined = CombinedCompleter(interpreter)

    if fuzzy:
        return FuzzyCompleter(combined)

    return combined


def create_magic_completer(
    extra_commands: Optional[Dict[str, str]] = None
) -> Completer:
    """
    Create a completer for magic commands only.

    Args:
        extra_commands: Additional commands to include

    Returns:
        Magic command Completer
    """
    commands = MAGIC_COMMANDS.copy()
    if extra_commands:
        commands.update(extra_commands)

    return FuzzyCompleter(MagicCommandCompleter(commands))
