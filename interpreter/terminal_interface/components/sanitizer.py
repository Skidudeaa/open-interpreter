"""
Terminal Output Sanitizer

Security layer to filter dangerous escape sequences from LLM output.

LLM output can contain terminal control sequences that:
- Overwrite clipboard content (OSC 52)
- Create malicious hyperlinks (OSC 8)
- Manipulate cursor/display state
- Execute terminal-specific commands

This module provides allowlist-based filtering that permits safe
formatting (colors, basic SGR) while blocking dangerous sequences.

Part of Phase 0: Foundation (must be implemented before other UI phases)

Usage:
    from .sanitizer import sanitize_output

    safe_text = sanitize_output(llm_response)
    console.print(safe_text)
"""

import re
from typing import Set, Optional
from enum import Enum, auto


class SanitizeLevel(Enum):
    """Sanitization strictness levels"""
    NONE = auto()       # No sanitization (dangerous, debug only)
    PERMISSIVE = auto() # Allow colors, block dangerous
    STRICT = auto()     # Strip ALL escape sequences


# SGR (Select Graphic Rendition) codes that are safe to allow
# These control text formatting: colors, bold, italic, etc.
SAFE_SGR_CODES: Set[int] = {
    0,      # Reset
    1,      # Bold
    2,      # Dim
    3,      # Italic
    4,      # Underline
    5,      # Slow blink
    7,      # Reverse
    8,      # Hidden
    9,      # Strikethrough
    22,     # Normal intensity
    23,     # Not italic
    24,     # Not underlined
    25,     # Not blinking
    27,     # Not reversed
    28,     # Not hidden
    29,     # Not strikethrough
    # Foreground colors (30-37, 90-97)
    *range(30, 38),
    *range(90, 98),
    # Background colors (40-47, 100-107)
    *range(40, 48),
    *range(100, 108),
    # Extended colors (38, 48 with 5;n or 2;r;g;b)
    38, 48,
    # Default colors
    39, 49,
}


# Regex patterns for escape sequences
# CSI: Control Sequence Introducer (ESC [)
CSI_PATTERN = re.compile(r'\x1b\[([0-9;]*)([A-Za-z])')

# OSC: Operating System Command (ESC ])
# These are the dangerous ones: clipboard, hyperlinks, title changes
OSC_PATTERN = re.compile(r'\x1b\]([0-9]+);([^\x07\x1b]*?)(?:\x07|\x1b\\)')

# Other escape sequences
ESC_PATTERN = re.compile(r'\x1b[^[\]][^\x1b]*')

# Full escape sequence (any ESC followed by stuff)
ANY_ESC_PATTERN = re.compile(r'\x1b(?:\[[0-9;]*[A-Za-z]|\][^\x07]*\x07|\][^\x1b]*\x1b\\|[^\[\]])')


def is_safe_sgr(params: str) -> bool:
    """
    Check if SGR parameters are safe.

    Args:
        params: The parameter string (e.g., "1;31" for bold red)

    Returns:
        True if all parameters are in the safe list
    """
    if not params:
        return True  # ESC[m is equivalent to ESC[0m (reset)

    try:
        codes = [int(p) for p in params.split(';') if p]
        return all(code in SAFE_SGR_CODES for code in codes)
    except ValueError:
        return False


def sanitize_csi(match: re.Match) -> str:
    """
    Process a CSI sequence and return safe version or empty string.

    Args:
        match: Regex match object for CSI sequence

    Returns:
        Original sequence if safe, empty string otherwise
    """
    params = match.group(1)
    command = match.group(2)

    # Only allow SGR (m command) with safe codes
    if command == 'm':
        if is_safe_sgr(params):
            return match.group(0)

    # Block all other CSI sequences:
    # - Cursor movement (A, B, C, D, H, etc.)
    # - Erase functions (J, K)
    # - Scroll (S, T)
    # - Mode changes (h, l)
    # - etc.
    return ''


def sanitize_osc(match: re.Match) -> str:
    """
    Process an OSC sequence - these are almost always blocked.

    Dangerous OSC codes:
    - OSC 0/1/2: Set window/icon title (mild nuisance)
    - OSC 8: Hyperlinks (can be malicious)
    - OSC 52: Clipboard manipulation (security risk!)
    - OSC 4: Color palette changes
    - OSC 104: Reset color

    Args:
        match: Regex match object for OSC sequence

    Returns:
        Empty string (OSC sequences are blocked by default)
    """
    # Block ALL OSC sequences
    # Could selectively allow OSC 0/1/2 (title) but safer to block
    return ''


def sanitize_output(
    text: str,
    level: SanitizeLevel = SanitizeLevel.PERMISSIVE
) -> str:
    """
    Sanitize terminal output to remove dangerous escape sequences.

    Args:
        text: Raw text potentially containing escape sequences
        level: Sanitization strictness level

    Returns:
        Sanitized text safe for terminal display

    Example:
        >>> sanitize_output("\\x1b[31mHello\\x1b[0m")  # Red text
        '\\x1b[31mHello\\x1b[0m'  # Allowed

        >>> sanitize_output("\\x1b]52;c;SGVsbG8=\\x07")  # Clipboard write
        ''  # Blocked!

        >>> sanitize_output("\\x1b]8;;http://evil.com\\x07Click\\x1b]8;;\\x07")
        'Click'  # Hyperlink stripped, text preserved
    """
    if level == SanitizeLevel.NONE:
        return text

    if level == SanitizeLevel.STRICT:
        # Remove ALL escape sequences
        return ANY_ESC_PATTERN.sub('', text)

    # PERMISSIVE: Allow safe SGR, block everything else
    # First, process OSC sequences (dangerous)
    text = OSC_PATTERN.sub(sanitize_osc, text)

    # Then, process CSI sequences (filter by command)
    text = CSI_PATTERN.sub(sanitize_csi, text)

    # Finally, remove any remaining escape sequences we missed
    text = ESC_PATTERN.sub('', text)

    return text


def strip_ansi(text: str) -> str:
    """
    Remove ALL ANSI escape sequences from text.

    Useful when you need plain text (e.g., for logging, file output).

    Args:
        text: Text with potential ANSI sequences

    Returns:
        Plain text with all escape sequences removed
    """
    return ANY_ESC_PATTERN.sub('', text)


def has_dangerous_sequences(text: str) -> bool:
    """
    Check if text contains potentially dangerous escape sequences.

    Useful for warning or logging before sanitization.

    Args:
        text: Text to check

    Returns:
        True if dangerous sequences detected
    """
    # Check for OSC sequences (always suspicious)
    if OSC_PATTERN.search(text):
        return True

    # Check for non-SGR CSI sequences
    for match in CSI_PATTERN.finditer(text):
        if match.group(2) != 'm':  # Not a color/formatting code
            return True
        if not is_safe_sgr(match.group(1)):
            return True

    return False


def get_sanitization_report(text: str) -> dict:
    """
    Generate a report of escape sequences found in text.

    Useful for debugging or security auditing.

    Args:
        text: Text to analyze

    Returns:
        Dictionary with counts and examples of sequences found
    """
    report = {
        "osc_sequences": [],
        "csi_sequences": [],
        "other_escapes": [],
        "has_dangerous": False,
    }

    for match in OSC_PATTERN.finditer(text):
        report["osc_sequences"].append({
            "code": match.group(1),
            "content": match.group(2)[:50] + "..." if len(match.group(2)) > 50 else match.group(2),
        })
        report["has_dangerous"] = True

    for match in CSI_PATTERN.finditer(text):
        seq_info = {
            "params": match.group(1),
            "command": match.group(2),
            "safe": match.group(2) == 'm' and is_safe_sgr(match.group(1)),
        }
        report["csi_sequences"].append(seq_info)
        if not seq_info["safe"]:
            report["has_dangerous"] = True

    for match in ESC_PATTERN.finditer(text):
        report["other_escapes"].append(repr(match.group(0)[:20]))
        report["has_dangerous"] = True

    return report


# Export public API
__all__ = [
    "sanitize_output",
    "strip_ansi",
    "has_dangerous_sequences",
    "get_sanitization_report",
    "SanitizeLevel",
]
