"""
Matrix Rain Background for Open Interpreter TUI.

ARCHITECTURE: Injects rain rendering into the Screen's render_line() method
so the rain becomes the screen's own background. Child widgets with
transparent backgrounds show rain through them; opaque backgrounds hide it.

WHY: Textual's CSS layer system doesn't alpha-composite between layers.
The screen render_line approach uses parent-child compositing, which Textual
fully supports — transparent children reveal the parent (screen) content.

TRADEOFF: ~12 FPS timer refreshes the entire screen. Efficient segment
grouping in render_line keeps per-frame cost low.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from rich.segment import Segment
from rich.style import Style
from textual.strip import Strip

if TYPE_CHECKING:
    from textual.app import App
    from textual.screen import Screen

# Half-width katakana + alphanumeric — all guaranteed single-cell width.
MATRIX_CHARS = (
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "\uff66\uff71\uff73\uff74\uff75\uff76\uff77\uff78\uff79\uff7a"
    "\uff7b\uff7c\uff7d\uff7e\uff7f\uff80\uff81\uff82\uff83\uff84"
    "\uff85\uff86\uff87\uff88\uff89\uff8a\uff8b\uff8c\uff8d\uff8e"
    "\uff8f\uff90\uff91\uff92\uff93\uff94\uff95\uff96\uff97\uff98"
    "\uff99\uff9a\uff9b\uff9c\uff9d"
)

# Pre-computed green gradient: 0 = dimmest tail, 9 = bright head.
_GRADIENT: list[Style] = [
    Style(color="#003300"),
    Style(color="#004d00"),
    Style(color="#006600"),
    Style(color="#008800"),
    Style(color="#00aa00"),
    Style(color="#00bb00"),
    Style(color="#00dd00"),
    Style(color="#00ff00"),
    Style(color="#00ff41"),
    Style(color="#ccffcc", bold=True),
]
_EMPTY_STYLE = Style()
_N_LEVELS = len(_GRADIENT)


class MatrixRainState:
    """
    Headless rain simulation state.

    Manages the column-drop particle system and grid independently of
    any Textual widget. Attached to a Screen via install_matrix_rain().
    """

    def __init__(self) -> None:
        self._cols: int = 0
        self._rows: int = 0
        self._drops: list[int] = []
        self._grid: list[list[tuple[str, int]]] = []
        self.enabled: bool = True

    def resize(self, width: int, height: int) -> None:
        """Grow or shrink the grid to match terminal dimensions."""
        if width <= 0 or height <= 0:
            return
        if width == self._cols and height == self._rows:
            return

        self._cols, self._rows = width, height

        while len(self._drops) < width:
            self._drops.append(random.randint(-height, height))
        if len(self._drops) > width:
            self._drops = self._drops[:width]

        while len(self._grid) < height:
            self._grid.append([("", 0)] * width)
        if len(self._grid) > height:
            self._grid = self._grid[:height]

        for i, row in enumerate(self._grid):
            if len(row) < width:
                self._grid[i] = row + [("", 0)] * (width - len(row))
            elif len(row) > width:
                self._grid[i] = row[:width]

    def tick(self) -> None:
        """Advance rain by one frame."""
        if not self.enabled:
            return
        cols, rows = self._cols, self._rows
        if cols <= 0 or rows <= 0:
            return

        grid = self._grid
        choice = random.choice
        rnd = random.random

        for r in range(rows):
            row = grid[r]
            for c in range(cols):
                ch, b = row[c]
                if b > 0:
                    if rnd() < 0.03:
                        ch = choice(MATRIX_CHARS)
                    row[c] = (ch, b - 1)

        for c in range(cols):
            r = self._drops[c]
            if 0 <= r < rows:
                grid[r][c] = (choice(MATRIX_CHARS), _N_LEVELS - 1)
            self._drops[c] = r + 1
            if self._drops[c] > rows and rnd() > 0.975:
                self._drops[c] = random.randint(-rows // 2, -1)

    def render_line(self, y: int, width: int) -> Strip:
        """Render one row as a Textual Strip."""
        if y >= len(self._grid) or not self._grid or width <= 0:
            return Strip.blank(width)

        row = self._grid[y]
        segs: list[Segment] = []
        buf: list[str] = []
        cur_style: Style = _EMPTY_STYLE

        for idx in range(min(width, len(row))):
            ch, b = row[idx]
            if b <= 0:
                style = _EMPTY_STYLE
                char = " "
            else:
                style = _GRADIENT[min(b, _N_LEVELS - 1)]
                char = ch or " "

            if style is cur_style:
                buf.append(char)
            else:
                if buf:
                    segs.append(Segment("".join(buf), cur_style))
                buf = [char]
                cur_style = style

        if buf:
            segs.append(Segment("".join(buf), cur_style))

        total = sum(len(s.text) for s in segs)
        if total < width:
            segs.append(Segment(" " * (width - total), _EMPTY_STYLE))

        return Strip(segs, cell_length=width)

    def clear(self) -> None:
        """Blank the grid immediately."""
        for r in range(len(self._grid)):
            self._grid[r] = [("", 0)] * self._cols


def install_matrix_rain(app: App) -> MatrixRainState:
    """
    Install Matrix rain as the app's screen background.

    Call from InterpreterTUI.on_mount(). Returns the state object
    so the caller can toggle ``state.enabled``.

    ARCHITECTURE: Monkey-patches the Screen's render_line() so rain
    becomes the screen's own background content. Children with
    transparent backgrounds reveal the rain; opaque ones hide it.
    """
    state = MatrixRainState()
    screen: Screen = app.screen

    # Patch render_line on the screen instance
    def _rain_render_line(y: int) -> Strip:
        w = screen.size.width
        # Lazy-init: screen size may be 0 during early mount
        if state._cols == 0 and w > 0:
            state.resize(w, screen.size.height)
        return state.render_line(y, w)

    screen.render_line = _rain_render_line  # type: ignore[assignment]

    # Timer: advance animation + auto-resize if terminal changed
    def _tick() -> None:
        w, h = screen.size.width, screen.size.height
        if w > 0 and h > 0:
            state.resize(w, h)  # no-op if size unchanged
            if state.enabled:
                state.tick()
                screen.refresh()

    app.set_interval(1 / 12, _tick)

    return state
