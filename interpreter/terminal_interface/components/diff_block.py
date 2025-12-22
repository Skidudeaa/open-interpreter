"""
Diff Block - Displays before/after code comparison.

Features:
- Side-by-side or unified diff view
- Syntax highlighting
- Line numbers
- Added/removed line highlighting
"""

import difflib
from typing import List, Tuple

from rich.console import Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .base_block import BaseBlock
from .theme import THEME, BOX_STYLES


class DiffBlock(BaseBlock):
    """
    Displays code differences with highlighting.

    Visual structure (unified):
    ┏━ 📝 Code Changes ━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
    ┃   def hello():                               ┃
    ┃ -     print("Hello")                         ┃
    ┃ +     print("Hello, World!")                 ┃
    ┃       return True                            ┃
    ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
    """

    def __init__(self, language: str = "python"):
        super().__init__()
        self.language = language
        self.old_code: str = ""
        self.new_code: str = ""
        self.context_lines: int = 3

    def set_diff(self, old_code: str, new_code: str):
        """Set the before and after code."""
        self.old_code = old_code
        self.new_code = new_code

    def get_unified_diff(self) -> List[Tuple[str, str]]:
        """
        Generate unified diff with line types.

        Returns:
            List of (line_type, line_content) tuples
            line_type is one of: 'context', 'added', 'removed', 'header'
        """
        old_lines = self.old_code.splitlines(keepends=True)
        new_lines = self.new_code.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile='before',
            tofile='after',
            n=self.context_lines,
        )

        result = []
        for line in diff:
            line = line.rstrip('\n')
            if line.startswith('+++') or line.startswith('---'):
                result.append(('header', line))
            elif line.startswith('@@'):
                result.append(('header', line))
            elif line.startswith('+'):
                result.append(('added', line))
            elif line.startswith('-'):
                result.append(('removed', line))
            else:
                result.append(('context', line))

        return result

    def get_stats(self) -> Tuple[int, int]:
        """Get number of added and removed lines."""
        diff = self.get_unified_diff()
        added = sum(1 for t, _ in diff if t == 'added')
        removed = sum(1 for t, _ in diff if t == 'removed')
        return added, removed

    def refresh(self, cursor: bool = True):
        """Refresh the diff block display."""
        if not self.old_code and not self.new_code:
            return

        components = []
        components.append(Text(""))  # Spacing

        # Diff panel
        diff_panel = self._build_diff_panel()
        components.append(diff_panel)

        # Stats footer
        stats = self._build_stats()
        components.append(stats)

        # Combine and display
        group = Group(*components)
        self.live.update(group)
        self.live.refresh()

    def _build_diff_panel(self) -> Panel:
        """Build the diff panel with syntax highlighting."""
        diff_lines = self.get_unified_diff()

        if not diff_lines:
            content = Text("No changes detected", style="dim italic")
            return Panel(
                content,
                title=" 📝 Code Changes ",
                title_align="left",
                box=BOX_STYLES["code"],
                border_style=THEME["text_muted"],
                padding=(0, 1),
            )

        styled_content = Text()

        for i, (line_type, line) in enumerate(diff_lines):
            if i > 0:
                styled_content.append("\n")

            if line_type == 'header':
                styled_content.append(line, style=f"bold {THEME['info']}")
            elif line_type == 'added':
                styled_content.append(line, style=f"bold {THEME['success']} on #0D3320")
            elif line_type == 'removed':
                styled_content.append(line, style=f"bold {THEME['error']} on #3D1A1A")
            else:  # context
                styled_content.append(line, style=THEME["text_secondary"])

        return Panel(
            styled_content,
            title=" 📝 Code Changes ",
            title_align="left",
            box=BOX_STYLES["code"],
            style=f"on {THEME['bg_medium']}",
            border_style=THEME["primary"],
            padding=(0, 1),
        )

    def _build_stats(self) -> Text:
        """Build the stats footer."""
        added, removed = self.get_stats()

        stats = Text("  ")
        if added > 0:
            stats.append(f"+{added}", style=f"bold {THEME['success']}")
            stats.append(" added  ", style="dim")
        if removed > 0:
            stats.append(f"-{removed}", style=f"bold {THEME['error']}")
            stats.append(" removed", style="dim")

        if added == 0 and removed == 0:
            stats.append("No changes", style="dim italic")

        return stats


class SideBySideDiff(DiffBlock):
    """Side-by-side diff view for wider terminals."""

    def _build_diff_panel(self) -> Panel:
        """Build side-by-side diff panel."""
        old_lines = self.old_code.splitlines()
        new_lines = self.new_code.splitlines()

        # Use difflib to match lines
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)

        table = Table(
            show_header=True,
            header_style=f"bold {THEME['text_muted']}",
            box=None,
            padding=(0, 1),
            expand=True,
        )
        table.add_column("Before", ratio=1)
        table.add_column("After", ratio=1)

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                for old, new in zip(old_lines[i1:i2], new_lines[j1:j2]):
                    table.add_row(
                        Text(old, style=THEME["text_secondary"]),
                        Text(new, style=THEME["text_secondary"]),
                    )
            elif tag == 'replace':
                max_len = max(i2 - i1, j2 - j1)
                old_slice = old_lines[i1:i2] + [''] * (max_len - (i2 - i1))
                new_slice = new_lines[j1:j2] + [''] * (max_len - (j2 - j1))
                for old, new in zip(old_slice, new_slice):
                    table.add_row(
                        Text(old, style=f"{THEME['error']} on #3D1A1A") if old else Text(""),
                        Text(new, style=f"{THEME['success']} on #0D3320") if new else Text(""),
                    )
            elif tag == 'delete':
                for old in old_lines[i1:i2]:
                    table.add_row(
                        Text(old, style=f"{THEME['error']} on #3D1A1A"),
                        Text("", style="dim"),
                    )
            elif tag == 'insert':
                for new in new_lines[j1:j2]:
                    table.add_row(
                        Text("", style="dim"),
                        Text(new, style=f"{THEME['success']} on #0D3320"),
                    )

        return Panel(
            table,
            title=" 📝 Code Changes (Side-by-Side) ",
            title_align="left",
            box=BOX_STYLES["code"],
            style=f"on {THEME['bg_medium']}",
            border_style=THEME["primary"],
            padding=(0, 1),
        )


def show_diff(old_code: str, new_code: str, language: str = "python", side_by_side: bool = False):
    """
    Convenience function to display a code diff.

    Args:
        old_code: Original code
        new_code: Modified code
        language: Programming language for syntax hints
        side_by_side: Use side-by-side view instead of unified
    """
    if side_by_side:
        block = SideBySideDiff(language)
    else:
        block = DiffBlock(language)

    block.set_diff(old_code, new_code)
    block.refresh()
    block.end()
