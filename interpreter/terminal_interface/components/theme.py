"""
Cyber Professional Theme - Terminal UI Color Palette

A modern, high-contrast palette with violet/cyan accents on slate backgrounds.
"""

# Color palette
THEME = {
    # Primary accents
    "primary": "#7C3AED",        # Violet - main accent
    "secondary": "#06B6D4",      # Cyan - secondary accent
    "success": "#10B981",        # Emerald - success states
    "warning": "#F59E0B",        # Amber - warnings
    "error": "#EF4444",          # Red - errors
    "info": "#3B82F6",           # Blue - informational

    # Background tones (Slate palette)
    "bg_dark": "#0F172A",        # Slate 900 - darkest background
    "bg_medium": "#1E293B",      # Slate 800 - code blocks
    "bg_light": "#334155",       # Slate 700 - output panels
    "bg_highlight": "#475569",   # Slate 600 - highlights

    # Text colors
    "text_primary": "#F8FAFC",   # Slate 50 - primary text
    "text_secondary": "#CBD5E1", # Slate 300 - secondary text
    "text_muted": "#64748B",     # Slate 500 - muted/dim text

    # Role-specific colors
    "assistant": "#22D3EE",      # Cyan 400 - assistant messages
    "user": "#A78BFA",           # Violet 400 - user messages
    "computer": "#4ADE80",       # Green 400 - computer output
    "system": "#FBBF24",         # Amber 400 - system messages

    # Syntax highlighting
    "code_theme": "one-dark",    # Rich syntax theme
}

# Role icons (emoji-based)
ROLE_ICONS = {
    "assistant": "\U0001F916",   # Robot
    "user": "\U0001F464",        # Bust in silhouette
    "computer": "\U0001F4BB",    # Laptop
    "system": "\u2699\uFE0F",    # Gear
}

# Language icons for code blocks
LANGUAGE_ICONS = {
    "python": "\U0001F40D",      # Snake
    "javascript": "\U0001F4DC", # Scroll
    "typescript": "\U0001F4D8", # Blue book
    "bash": "\U0001F4BB",       # Laptop
    "shell": "\U0001F4BB",      # Laptop
    "sh": "\U0001F4BB",         # Laptop
    "zsh": "\U0001F4BB",        # Laptop
    "html": "\U0001F310",       # Globe
    "css": "\U0001F3A8",        # Palette
    "sql": "\U0001F5C3\uFE0F",  # Card file box
    "r": "\U0001F4CA",          # Bar chart
    "ruby": "\U0001F48E",       # Gem
    "go": "\U0001F537",         # Blue diamond
    "rust": "\u2699\uFE0F",     # Gear
    "java": "\u2615",           # Coffee
    "c": "\U0001F1E8",          # C regional indicator
    "cpp": "\U0001F1E8",        # C regional indicator
    "applescript": "\U0001F34E", # Apple
}

# Status icons for code execution
STATUS_ICONS = {
    "pending": ("\u23F3", "warning"),     # Hourglass, yellow
    "running": ("\u25B6\uFE0F", "secondary"),  # Play, cyan
    "success": ("\u2705", "success"),     # Check mark, green
    "error": ("\u274C", "error"),         # Cross mark, red
}

# Prompt symbols
PROMPT_SYMBOLS = {
    "default": "\u276F",         # Heavy right-pointing angle quotation mark
    "multiline": "\u226B",       # Much greater-than
    "confirmation": "?",
}

# Rich box styles to use
from rich import box
BOX_STYLES = {
    "message": box.ROUNDED,
    "code": box.HEAVY,
    "output": box.SIMPLE,
    "status": box.MINIMAL,
}


def get_role_style(role: str) -> str:
    """Get the Rich style string for a given role."""
    color = THEME.get(role, THEME["text_primary"])
    return f"bold {color}"


def get_role_icon(role: str) -> str:
    """Get the emoji icon for a given role."""
    return ROLE_ICONS.get(role, "\U0001F4AC")  # Default: speech balloon


def get_language_icon(language: str) -> str:
    """Get the emoji icon for a programming language."""
    return LANGUAGE_ICONS.get(language.lower(), "\U0001F4C4")  # Default: page facing up


def get_status_display(status: str) -> tuple:
    """Get (icon, theme_color_key) for an execution status."""
    return STATUS_ICONS.get(status, ("\u2753", "text_muted"))  # Default: question mark
