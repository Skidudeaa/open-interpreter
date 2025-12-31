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
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .theme import THEME


class TableDisplay:
    """
    Displays structured data as formatted tables.

    Usage:
        table = TableDisplay()
        table.from_dicts([{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}])
        table.show()
    """

    def __init__(self, title: str | None = None, max_rows: int = 20):
        self.title = title
        self.max_rows = max_rows
        self.columns: list[str] = []
        self.rows: list[list[Any]] = []
        self.console = Console()

    def from_dicts(self, data: list[dict[str, Any]]):
        """Load data from list of dictionaries."""
        if not data:
            return

        # Get all unique keys as columns
        self.columns = list(dict.fromkeys(key for row in data for key in row.keys()))

        # Extract rows
        self.rows = [[row.get(col, "") for col in self.columns] for row in data]

    def from_list_of_lists(
        self, data: list[list[Any]], headers: list[str] | None = None
    ):
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
        try:
            reader = csv.reader(io.StringIO(csv_text))
            rows = list(reader)
            if rows:
                self.from_list_of_lists(rows)
        except csv.Error:
            # Malformed CSV (e.g., embedded newlines in unquoted fields)
            pass

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
        footer.append(
            f"\n  Showing rows {showing_start}-{showing_end} of {total}", style="dim"
        )

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
                if key in ("q", "quit", "exit"):
                    break
                elif key in ("n", "next", ""):
                    current_page = min(current_page + 1, total_pages - 1)
                elif key in ("p", "prev", "previous"):
                    current_page = max(current_page - 1, 0)
                elif key.isdigit():
                    page = int(key) - 1
                    if 0 <= page < total_pages:
                        current_page = page
            except (KeyboardInterrupt, EOFError):
                break


def format_sql_result(
    rows: list[tuple], columns: list[str] | None = None, title: str = "Query Result"
) -> str:
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


def _looks_like_csv(output: str) -> bool:
    """
    Robust CSV detection using multiple heuristics.

    Returns True only if output strongly appears to be CSV data,
    avoiding false positives on logs, stack traces, prose, etc.
    """
    lines = output.strip().split("\n")

    # Need at least 2 lines (header + 1 data row)
    if len(lines) < 2:
        return False

    # Reject if any line is too long (likely prose or logs)
    if any(len(line) > 500 for line in lines[:10]):
        return False

    # Reject common non-CSV patterns
    non_csv_indicators = [
        "Traceback (most recent call last)",  # Python stack trace
        'File "',  # Stack trace file refs
        "Error:",  # Error messages
        "Exception:",  # Exception messages
        "WARNING:",  # Log output
        "INFO:",  # Log output
        "DEBUG:",  # Log output
        ">>>",  # Python REPL
        "...",  # Continuation/progress
        "━",  # Progress bars (Rich)
        "─",  # Box drawing
        "│",  # Box drawing
        "├",  # Box drawing
        "└",  # Box drawing
        "Collecting ",  # pip install
        "Downloading ",  # pip install
        "Installing ",  # pip install
        "Successfully installed",  # pip install
        "Requirement already",  # pip install
        "npm ",  # npm output
        "yarn ",  # yarn output
        "git ",  # git output
        "fatal:",  # git errors
        "  at ",  # JS stack traces
        "    at ",  # JS stack traces
    ]

    full_text = output[:2000]  # Check first 2KB
    if any(indicator in full_text for indicator in non_csv_indicators):
        return False

    # Use csv.Sniffer to detect if it looks like CSV
    try:
        sample = "\n".join(lines[:10])
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        return False

    # Parse and validate structure
    try:
        reader = csv.reader(io.StringIO(output), dialect)
        rows = list(reader)
    except csv.Error:
        return False

    if len(rows) < 2:
        return False

    header = rows[0]
    data_rows = rows[1:]

    # Need at least 2 columns
    if len(header) < 2:
        return False

    # Headers should look like column names (not sentences)
    for col in header:
        col = col.strip()
        # Reject if header looks like a sentence (too many words or too long)
        if len(col) > 50 or col.count(" ") > 4:
            return False
        # Reject if header contains sentence-ending punctuation
        if col.endswith((".", "!", "?")):
            return False

    # Check row consistency - most rows should have same column count
    col_count = len(header)
    matching_rows = sum(1 for row in data_rows if len(row) == col_count)
    if matching_rows < len(data_rows) * 0.8:  # 80% must match
        return False

    # Check that fields are reasonably sized (not prose paragraphs)
    for row in data_rows[:5]:
        for field in row:
            if len(field) > 200:  # Single field too long
                return False

    return True


def detect_and_format_table(output: str) -> str | None:
    """
    Detect if output contains tabular data and format it.

    Args:
        output: Raw output text

    Returns:
        Formatted table string if table detected, None otherwise
    """
    # Quick rejection - need comma/tab and newline for CSV
    if not (("," in output or "\t" in output) and "\n" in output):
        pass  # Skip CSV detection
    elif _looks_like_csv(output):
        try:
            table = TableDisplay()
            table.from_csv(output)
            if table.columns and len(table.columns) > 1 and len(table.rows) > 0:
                console = Console(force_terminal=True)
                with console.capture() as capture:
                    table.show()
                return capture.get()
        except Exception:
            pass  # Not valid CSV, continue

    # Try to detect JSON array
    stripped = output.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            data = json.loads(output)
            if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
                table = TableDisplay()
                table.from_dicts(data)
                if table.columns and len(table.columns) > 1:
                    console = Console(force_terminal=True)
                    with console.capture() as capture:
                        table.show()
                    return capture.get()
        except json.JSONDecodeError:
            pass

    return None
