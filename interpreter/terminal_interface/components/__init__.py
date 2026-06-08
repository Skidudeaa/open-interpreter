"""
Terminal Interface Components

Visual blocks and UI elements for the Open Interpreter CLI.
Cyber Professional theme with violet/cyan accents.
"""

from .base_block import BaseBlock
from .code_block import CodeBlock
from .diff_block import DiffBlock, show_diff

# New UI components (v0.4.x)
from .error_block import ErrorBlock, display_error
from .globe_spinner import GlobeSpinner
from .interactive_menu import InteractiveMenu, interactive_choice
from .live_output_panel import LiveOutputPanel, OutputBuffer
from .message_block import MessageBlock, textify_markdown_code_blocks
from .network_status import NetworkStatus, get_network_status
from .prompt_block import PromptBlock, styled_confirm, styled_input
from .spinner_block import ExecutingSpinner, SpinnerBlock, ThinkingSpinner, with_spinner
from .status_bar import StatusBar, display_status_bar
from .table_display import TableDisplay, detect_and_format_table
from .theme import (
    BOX_STYLES,
    LANGUAGE_ICONS,
    PROMPT_SYMBOLS,
    ROLE_ICONS,
    STATUS_ICONS,
    THEME,
    get_language_icon,
    get_role_icon,
    get_role_style,
    get_status_display,
)

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
    "GlobeSpinner",
    "with_spinner",
    # Status
    "StatusBar",
    "display_status_bar",
    # Error display
    "ErrorBlock",
    "display_error",
    # Diff display
    "DiffBlock",
    "show_diff",
    # Interactive menus
    "InteractiveMenu",
    "interactive_choice",
    # Table formatting
    "TableDisplay",
    "detect_and_format_table",
    # Network status
    "NetworkStatus",
    "get_network_status",
]
