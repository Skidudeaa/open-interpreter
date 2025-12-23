"""
Theme System - Terminal UI Color Palettes

Supports multiple themes: dark (default), light, high-contrast.
Theme can be set via environment variable OI_THEME or interpreter.theme.
"""

import os

# Theme definitions
THEMES = {
    "dark": {
        # Primary accents
        "primary": "#7C3AED",  # Violet - main accent
        "secondary": "#06B6D4",  # Cyan - secondary accent
        "success": "#10B981",  # Emerald - success states
        "warning": "#F59E0B",  # Amber - warnings
        "error": "#EF4444",  # Red - errors
        "info": "#3B82F6",  # Blue - informational
        # Background tones (Slate palette)
        "bg_dark": "#0F172A",  # Slate 900 - darkest background
        "bg_medium": "#1E293B",  # Slate 800 - code blocks
        "bg_light": "#334155",  # Slate 700 - output panels
        "bg_highlight": "#475569",  # Slate 600 - highlights
        # Text colors
        "text_primary": "#F8FAFC",  # Slate 50 - primary text
        "text_secondary": "#CBD5E1",  # Slate 300 - secondary text
        "text_muted": "#64748B",  # Slate 500 - muted/dim text
        # Role-specific colors
        "assistant": "#22D3EE",  # Cyan 400 - assistant messages
        "user": "#A78BFA",  # Violet 400 - user messages
        "computer": "#4ADE80",  # Green 400 - computer output
        "system": "#FBBF24",  # Amber 400 - system messages
        # Syntax highlighting
        "code_theme": "one-dark",  # Rich syntax theme
    },
    "light": {
        # Primary accents (darker for light backgrounds)
        "primary": "#6D28D9",  # Violet 700
        "secondary": "#0891B2",  # Cyan 600
        "success": "#059669",  # Emerald 600
        "warning": "#D97706",  # Amber 600
        "error": "#DC2626",  # Red 600
        "info": "#2563EB",  # Blue 600
        # Background tones (light grays)
        "bg_dark": "#F8FAFC",  # Slate 50
        "bg_medium": "#F1F5F9",  # Slate 100
        "bg_light": "#E2E8F0",  # Slate 200
        "bg_highlight": "#CBD5E1",  # Slate 300
        # Text colors (dark for light backgrounds)
        "text_primary": "#0F172A",  # Slate 900
        "text_secondary": "#334155",  # Slate 700
        "text_muted": "#64748B",  # Slate 500
        # Role-specific colors (darker)
        "assistant": "#0891B2",  # Cyan 600
        "user": "#7C3AED",  # Violet 600
        "computer": "#059669",  # Emerald 600
        "system": "#D97706",  # Amber 600
        # Syntax highlighting
        "code_theme": "github-dark",  # Rich syntax theme for light mode
    },
    "high-contrast": {
        # High contrast accents (maximum visibility)
        "primary": "#A855F7",  # Bright violet
        "secondary": "#22D3EE",  # Bright cyan
        "success": "#22C55E",  # Bright green
        "warning": "#FBBF24",  # Bright amber
        "error": "#F87171",  # Bright red
        "info": "#60A5FA",  # Bright blue
        # Background tones (pure black/white)
        "bg_dark": "#000000",  # Pure black
        "bg_medium": "#0A0A0A",  # Near black
        "bg_light": "#171717",  # Dark gray
        "bg_highlight": "#262626",  # Highlight gray
        # Text colors (maximum contrast)
        "text_primary": "#FFFFFF",  # Pure white
        "text_secondary": "#E5E5E5",  # Light gray
        "text_muted": "#A3A3A3",  # Medium gray
        # Role-specific colors (bright, distinct)
        "assistant": "#22D3EE",  # Bright cyan
        "user": "#C084FC",  # Bright violet
        "computer": "#4ADE80",  # Bright green
        "system": "#FCD34D",  # Bright yellow
        # Syntax highlighting
        "code_theme": "monokai",  # High contrast theme
    },
}


def get_current_theme_name() -> str:
    """Get the current theme name from environment or default."""
    return os.environ.get("OI_THEME", "dark").lower()


def get_theme(theme_name: str = None) -> dict:
    """Get a theme by name, defaulting to dark theme."""
    if theme_name is None:
        theme_name = get_current_theme_name()
    return THEMES.get(theme_name, THEMES["dark"])


# Active theme (can be changed at runtime)
THEME = get_theme()

# Role icons (emoji-based)
ROLE_ICONS = {
    "assistant": "\U0001f916",  # Robot
    "user": "\U0001f464",  # Bust in silhouette
    "computer": "\U0001f4bb",  # Laptop
    "system": "\u2699\ufe0f",  # Gear
}

# Language icons for code blocks
LANGUAGE_ICONS = {
    "python": "\U0001f40d",  # Snake
    "javascript": "\U0001f4dc",  # Scroll
    "typescript": "\U0001f4d8",  # Blue book
    "bash": "\U0001f4bb",  # Laptop
    "shell": "\U0001f4bb",  # Laptop
    "sh": "\U0001f4bb",  # Laptop
    "zsh": "\U0001f4bb",  # Laptop
    "html": "\U0001f310",  # Globe
    "css": "\U0001f3a8",  # Palette
    "sql": "\U0001f5c3\ufe0f",  # Card file box
    "r": "\U0001f4ca",  # Bar chart
    "ruby": "\U0001f48e",  # Gem
    "go": "\U0001f537",  # Blue diamond
    "rust": "\u2699\ufe0f",  # Gear
    "java": "\u2615",  # Coffee
    "c": "\U0001f1e8",  # C regional indicator
    "cpp": "\U0001f1e8",  # C regional indicator
    "applescript": "\U0001f34e",  # Apple
}

# Status icons for code execution
STATUS_ICONS = {
    "pending": ("\u23f3", "warning"),  # Hourglass, yellow
    "running": ("\u25b6\ufe0f", "secondary"),  # Play, cyan
    "success": ("\u2705", "success"),  # Check mark, green
    "error": ("\u274c", "error"),  # Cross mark, red
}

# Prompt symbols
PROMPT_SYMBOLS = {
    "default": "\u276f",  # Heavy right-pointing angle quotation mark
    "multiline": "\u226b",  # Much greater-than
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
    return ROLE_ICONS.get(role, "\U0001f4ac")  # Default: speech balloon


def get_language_icon(language: str) -> str:
    """Get the emoji icon for a programming language."""
    return LANGUAGE_ICONS.get(language.lower(), "\U0001f4c4")  # Default: page facing up


def get_status_display(status: str) -> tuple:
    """Get (icon, theme_color_key) for an execution status."""
    return STATUS_ICONS.get(status, ("\u2753", "text_muted"))  # Default: question mark
