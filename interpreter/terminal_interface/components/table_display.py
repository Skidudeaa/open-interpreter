"""
Table Display - Formatted table output for structured data.

Features:
- Auto-column sizing
- SQL result formatting
- CSV/JSON rendering
- Pagination for large tables
"""

import csv
import io
import json
from typing import Any, Dict, List, Optional, Union

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .theme import THEME, BOX_STYLES


class TableDisplay:
    """
    Displays structured data as formatted tables.

    Usage:
        table = TableDisplay()
        table.from_dicts([{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}])
        table.show()
    """

    def __init__(self, title: str = None, max_rows: int = 20):
        self.title = title
        self.max_rows = max_rows
        self.columns: List[str] = []
        self.rows: List[List[Any]] = []
        self.console = Console()

    def from_dicts(self, data: List[Dict[str, Any]]):
        """Load data from list of dictionaries."""
        if not data:
            return

        # Get all unique keys as columns
        self.columns = list(dict.fromkeys(
            key for row in data for key in row.keys()
        ))

        # Extract rows
        self.rows = [
            [row.get(col, "") for col in self.columns]
            for row in data
        ]

    def from_list_of_lists(self, data: List[List[Any]], headers: List[str] = None):
        """Load data from list of lists with optional headers."""
        if not data:
            return

        if headers:
            self.columns = headers
            self.rows = data
        else:
            # First row is headers
            self.columns = [str(h) for h in data[0]]
            self.rows = data[1:]

    def from_csv(self, csv_text: str):
        """Parse CSV text into table."""
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        if rows:
            self.from_list_of_lists(rows)

    def from_json(self, json_text: str):
        """Parse JSON array into table."""
        try:
            data = json.loads(json_text)
            if isinstance(data, list):
                self.from_dicts(data)
        except json.JSONDecodeError:
            pass

    def _build_table(self, start_row: int = 0) -> Table:
        """Build Rich table for display."""
        table = Table(
            title=self.title,
            show_header=True,
            header_style=f"bold {THEME['secondary']}",
            border_style=THEME["text_muted"],
            row_styles=[f"on {THEME['bg_medium']}", f"on {THEME['bg_light']}"],
            expand=True,
            padding=(0, 1),
        )

        # Add columns
        for col in self.columns:
            table.add_column(str(col))

        # Add visible rows
        end_row = min(start_row + self.max_rows, len(self.rows))
        for row in self.rows[start_row:end_row]:
            table.add_row(*[str(cell) for cell in row])

        return table

    def show(self, start_row: int = 0):
        """Display the table."""
        if not self.columns:
            self.console.print("[dim]No data to display[/dim]")
            return

        table = self._build_table(start_row)

        # Add row count info
        total = len(self.rows)
        showing_start = start_row + 1
        showing_end = min(start_row + self.max_rows, total)

        footer = Text()
        footer.append(f"\n  Showing rows {showing_start}-{showing_end} of {total}", style="dim")

        if total > self.max_rows:
            footer.append("  (use pagination to see more)", style="dim italic")

        self.console.print(table)
        self.console.print(footer)


class PaginatedTable(TableDisplay):
    """Table with interactive pagination."""

    def show_paginated(self):
        """Show table with pagination controls."""
        if not self.columns:
            self.console.print("[dim]No data to display[/dim]")
            return

        current_page = 0
        total_pages = (len(self.rows) + self.max_rows - 1) // self.max_rows

        while True:
            # Clear and show current page
            self.console.clear()
            start_row = current_page * self.max_rows
            table = self._build_table(start_row)
            self.console.print(table)

            # Show pagination info
            self.console.print(
                f"\n  Page {current_page + 1}/{total_pages}  "
                f"[dim]← prev | next → | q quit[/dim]"
            )

            # Wait for input
            try:
                key = input().strip().lower()
                if key in ('q', 'quit', 'exit'):
                    break
                elif key in ('n', 'next', ''):
                    current_page = min(current_page + 1, total_pages - 1)
                elif key in ('p', 'prev', 'previous'):
                    current_page = max(current_page - 1, 0)
                elif key.isdigit():
                    page = int(key) - 1
                    if 0 <= page < total_pages:
                        current_page = page
            except (KeyboardInterrupt, EOFError):
                break


def format_sql_result(rows: List[tuple], columns: List[str] = None, title: str = "Query Result") -> str:
    """
    Format SQL query results as a displayable table.

    Args:
        rows: List of row tuples from cursor.fetchall()
        columns: Column names (from cursor.description)
        title: Table title

    Returns:
        Formatted table string
    """
    table = TableDisplay(title=title)

    if columns:
        table.from_list_of_lists([list(row) for row in rows], headers=columns)
    else:
        table.from_list_of_lists([list(row) for row in rows])

    # Capture output
    console = Console(force_terminal=True)
    with console.capture() as capture:
        table.show()

    return capture.get()


def detect_and_format_table(output: str) -> Optional[str]:
    """
    Detect if output contains tabular data and format it.

    Args:
        output: Raw output text

    Returns:
        Formatted table string if table detected, None otherwise
    """
    # Try to detect CSV
    if ',' in output and '\n' in output:
        lines = output.strip().split('\n')
        if all(line.count(',') == lines[0].count(',') for line in lines[:5]):
            table = TableDisplay()
            table.from_csv(output)
            if table.columns:
                console = Console(force_terminal=True)
                with console.capture() as capture:
                    table.show()
                return capture.get()

    # Try to detect JSON array
    if output.strip().startswith('['):
        try:
            data = json.loads(output)
            if isinstance(data, list) and data and isinstance(data[0], dict):
                table = TableDisplay()
                table.from_dicts(data)
                console = Console(force_terminal=True)
                with console.capture() as capture:
                    table.show()
                return capture.get()
        except json.JSONDecodeError:
            pass

    return None
