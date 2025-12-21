"""
Terminal Interface Components

Visual blocks and UI elements for the Open Interpreter CLI.
Cyber Professional theme with violet/cyan accents.
"""

from .theme import (
    THEME,
    ROLE_ICONS,
    LANGUAGE_ICONS,
    STATUS_ICONS,
    PROMPT_SYMBOLS,
    BOX_STYLES,
    get_role_style,
    get_role_icon,
    get_language_icon,
    get_status_display,
)
from .base_block import BaseBlock
from .message_block import MessageBlock, textify_markdown_code_blocks
from .code_block import CodeBlock
from .live_output_panel import LiveOutputPanel, OutputBuffer
from .prompt_block import PromptBlock, styled_input, styled_confirm
from .spinner_block import SpinnerBlock, ThinkingSpinner, ExecutingSpinner, with_spinner
from .status_bar import StatusBar, display_status_bar

__all__ = [
    # Theme
    "THEME",
    "ROLE_ICONS",
    "LANGUAGE_ICONS",
    "STATUS_ICONS",
    "PROMPT_SYMBOLS",
    "BOX_STYLES",
    "get_role_style",
    "get_role_icon",
    "get_language_icon",
    "get_status_display",
    # Blocks
    "BaseBlock",
    "MessageBlock",
    "CodeBlock",
    "textify_markdown_code_blocks",
    # Output handling
    "LiveOutputPanel",
    "OutputBuffer",
    # Input/Prompts
    "PromptBlock",
    "styled_input",
    "styled_confirm",
    # Spinners
    "SpinnerBlock",
    "ThinkingSpinner",
    "ExecutingSpinner",
    "with_spinner",
    # Status
    "StatusBar",
    "display_status_bar",
]
