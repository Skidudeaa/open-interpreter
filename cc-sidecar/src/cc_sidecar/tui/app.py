"""
cc-sidecar TUI — Textual-based terminal dashboard.

Shows live session state from the sidecar daemon via WebSocket.
Panels: Session, Agents, Context, Files, Alerts, Timeline.

Usage:
    cc-sidecar tui
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Static

from .panels import (
    AgentsPanel,
    AlertsPanel,
    ContextPanel,
    FilesPanel,
    SessionPanel,
    TimelinePanel,
)

logger = logging.getLogger(__name__)

# Refresh interval (seconds)
REFRESH_INTERVAL = 2.0

# Default WebSocket URL
DEFAULT_WS_URL = "ws://127.0.0.1:9340"


class SidecarDashboard(App):
    """Main TUI dashboard for cc-sidecar."""

    TITLE = "cc-sidecar"
    SUB_TITLE = "Claude Code Observability"

    CSS = """
    Screen {
        layout: vertical;
    }
    #main {
        layout: horizontal;
        height: 1fr;
    }
    #left-col {
        width: 60%;
        height: 100%;
    }
    #right-col {
        width: 40%;
        height: 100%;
    }
    .panel {
        border: round $secondary;
        margin: 0 0 1 0;
        padding: 0 1;
        height: auto;
        max-height: 30;
    }
    .panel-title {
        text-style: bold;
    }
    #session-panel {
        height: auto;
        max-height: 10;
    }
    #agents-panel {
        height: auto;
        max-height: 15;
    }
    #context-panel {
        height: auto;
        max-height: 15;
    }
    #files-panel {
        height: auto;
        max-height: 12;
    }
    #alerts-panel {
        height: auto;
        max-height: 10;
    }
    #timeline-panel {
        height: 1fr;
    }
    #status-bar {
        height: 1;
        dock: bottom;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("1", "focus_session", "Session", show=False),
        Binding("2", "focus_agents", "Agents", show=False),
        Binding("3", "focus_context", "Context", show=False),
        Binding("4", "focus_files", "Files", show=False),
        Binding("5", "focus_alerts", "Alerts", show=False),
        Binding("6", "focus_timeline", "Timeline", show=False),
    ]

    def __init__(self, ws_url: str = DEFAULT_WS_URL):
        super().__init__()
        self._ws_url = ws_url
        self._ws = None
        self._current_session_id: str | None = None
        self._connected = False
        # WHY: Store the WebSocket worker's asyncio loop so _request_full_refresh
        # (called from Textual's main thread) can schedule sends on the correct loop.
        self._ws_loop: asyncio.AbstractEventLoop | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            with VerticalScroll(id="left-col"):
                yield SessionPanel(id="session-panel", classes="panel")
                yield AgentsPanel(id="agents-panel", classes="panel")
                yield FilesPanel(id="files-panel", classes="panel")
                yield AlertsPanel(id="alerts-panel", classes="panel")
            with VerticalScroll(id="right-col"):
                yield ContextPanel(id="context-panel", classes="panel")
                yield TimelinePanel(id="timeline-panel", classes="panel")
        yield Static(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        self._update_status("Connecting to daemon...")
        self.set_interval(REFRESH_INTERVAL, self._periodic_refresh)
        self._connect_ws()

    @work(thread=True)
    def _connect_ws(self) -> None:
        """Connect to daemon WebSocket in a worker thread.

        WHY: Textual's @work(thread=True) expects a synchronous function.
        The previous async def here would run without an event loop, causing
        await calls to fail. We run the async logic via asyncio.run() and
        store the loop reference for cross-thread send scheduling.
        """
        try:
            asyncio.run(self._connect_ws_async())
        except Exception as e:
            self._connected = False
            self._ws_loop = None
            self.call_from_thread(self._update_status, f"Disconnected: {e}")
            self.call_from_thread(self._load_direct)

    async def _connect_ws_async(self) -> None:
        """Async WebSocket connection logic."""
        try:
            import websockets
        except ImportError:
            self.call_from_thread(self._update_status, "websockets not installed")
            self.call_from_thread(self._load_direct)
            return

        self._ws_loop = asyncio.get_running_loop()
        try:
            async with websockets.connect(self._ws_url) as ws:
                self._ws = ws
                self._connected = True
                self.call_from_thread(self._update_status, "Connected to daemon")

                # Get initial session list
                await ws.send(json.dumps({"type": "sessions"}))
                response = await ws.recv()
                data = json.loads(response)
                sessions = data.get("data", [])
                if sessions:
                    self._current_session_id = sessions[0].get("session_id")
                    self.call_from_thread(self._request_full_refresh)

                # Listen for push events
                async for message in ws:
                    try:
                        event = json.loads(message)
                        if event.get("type") == "event":
                            sid = event.get("session_id")
                            if not self._current_session_id:
                                self._current_session_id = sid
                            if sid == self._current_session_id:
                                self.call_from_thread(self._request_full_refresh)
                    except json.JSONDecodeError:
                        pass
        finally:
            self._connected = False
            self._ws_loop = None

    def _load_direct(self) -> None:
        """Load data directly from SQLite when WebSocket is unavailable."""
        try:
            from ..db.store import EventStore

            store = EventStore()
            sessions = store.get_sessions(limit=1)
            if sessions:
                self._current_session_id = sessions[0]["session_id"]
                summary = store.get_session_summary(self._current_session_id)
                self._update_panels(summary)
                self._update_status(f"Direct DB mode — {self._current_session_id[:12]}")
            else:
                self._update_status("No sessions found in database")
            store.close()
        except Exception as e:
            self._update_status(f"DB error: {e}")

    def _periodic_refresh(self) -> None:
        """Periodic refresh timer callback."""
        if self._connected and self._current_session_id:
            self._request_full_refresh()
        elif not self._connected:
            self._load_direct()

    def _request_full_refresh(self) -> None:
        """Request full state refresh from daemon.

        WHY: This is called from Textual's main thread, but the WebSocket
        lives on the worker thread's asyncio loop. We must schedule the send
        on _ws_loop (the worker's loop), not on Textual's loop.
        """
        loop = self._ws_loop
        if not self._ws or not self._current_session_id or loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._ws.send(
                    json.dumps(
                        {
                            "type": "session_summary",
                            "session_id": self._current_session_id,
                        }
                    )
                ),
                loop,
            )
        except Exception:
            pass

    def _update_panels(self, data: dict[str, Any]) -> None:
        """Update all panels with fresh data."""
        session_panel = self.query_one("#session-panel", SessionPanel)
        agents_panel = self.query_one("#agents-panel", AgentsPanel)
        context_panel = self.query_one("#context-panel", ContextPanel)
        files_panel = self.query_one("#files-panel", FilesPanel)
        alerts_panel = self.query_one("#alerts-panel", AlertsPanel)

        session_panel.update(session_panel.render_session(data))
        agents_panel.update(agents_panel.render_agents(data.get("agents", [])))
        context_panel.update(context_panel.render_context(data))
        files_panel.update(files_panel.render_files(data.get("files", [])))
        alerts_panel.update(alerts_panel.render_alerts(data.get("active_alerts", [])))

    def _update_status(self, message: str) -> None:
        """Update the status bar."""
        try:
            status = self.query_one("#status-bar", Static)
            sid = self._current_session_id or "none"
            status.update(f" {message}  |  session: {sid[:12]}")
        except Exception:
            pass

    def action_refresh(self) -> None:
        """Manual refresh."""
        self._periodic_refresh()

    def action_quit(self) -> None:
        self.exit()

    def action_focus_session(self) -> None:
        self.query_one("#session-panel").focus()

    def action_focus_agents(self) -> None:
        self.query_one("#agents-panel").focus()

    def action_focus_context(self) -> None:
        self.query_one("#context-panel").focus()

    def action_focus_files(self) -> None:
        self.query_one("#files-panel").focus()

    def action_focus_alerts(self) -> None:
        self.query_one("#alerts-panel").focus()

    def action_focus_timeline(self) -> None:
        self.query_one("#timeline-panel").focus()


def run_tui() -> int:
    """Entry point for cc-sidecar tui."""
    app = SidecarDashboard()
    app.run()
    return 0
