"""
cc-sidecar daemon — long-lived local daemon for event processing.

Components:
    - Unix socket listener (receives events from emit CLI and EventBus bridge)
    - Reducer loop (processes events into materialized state)
    - WebSocket server (pushes state updates to TUI and VS Code)
    - Spool replay (replays buffered events on startup)
    - Stuck/orphan detector (periodic health checks)

All localhost-only. No cloud telemetry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..db.store import EventStore
from ..ingest.transport import clear_spool, get_socket_path, read_spool_files
from ..reducer.state_machine import Reducer

logger = logging.getLogger(__name__)

# Default WebSocket port
DEFAULT_WS_PORT = 9340

# Stuck/orphan check interval (seconds)
HEALTH_CHECK_INTERVAL = 30


class SidecarDaemon:
    """Long-lived sidecar daemon."""

    def __init__(
        self,
        socket_path: str | Path | None = None,
        ws_port: int = DEFAULT_WS_PORT,
        db_path: str | Path | None = None,
    ):
        self._socket_path = Path(socket_path) if socket_path else get_socket_path()
        self._ws_port = ws_port
        self._store = EventStore(db_path)
        self._reducer = Reducer(self._store)
        self._running = False
        self._ws_clients: set[Any] = set()
        # WHY: _ws_clients is mutated from both the asyncio event loop thread
        # (_ws_handler add/discard) and from the socket listener thread pool
        # (_broadcast_ws iteration). A plain set is not thread-safe for
        # concurrent iteration + mutation.
        self._ws_lock = threading.Lock()
        # WHY: Store the asyncio loop reference set during _run_ws_server so
        # _broadcast_ws (called from thread pool) can safely schedule coroutines.
        # asyncio.get_event_loop() is deprecated from non-main threads in 3.10+
        # and raises RuntimeError in 3.12+.
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._event_seq = 0
        self._seq_lock = threading.Lock()
        # WHY: Bounded thread pool prevents resource exhaustion under sustained
        # load from many concurrent socket connections
        self._connection_pool = ThreadPoolExecutor(
            max_workers=16, thread_name_prefix="sidecar-conn"
        )

    def _next_seq(self) -> int:
        with self._seq_lock:
            self._event_seq += 1
            return self._event_seq

    # --- Event processing ---

    def process_event(self, envelope: dict[str, Any]) -> None:
        """Process an incoming event envelope."""
        try:
            event_name = envelope.get("event_name", "Unknown")
            session_id = envelope.get("session_id", "unknown")
            payload = envelope.get("payload", {})
            received_at_ms = envelope.get("received_at_ms", int(time.time() * 1000))
            seq = envelope.get("seq", self._next_seq())

            # Store raw event
            row_id = self._store.insert_raw_event(
                received_at_ms=received_at_ms,
                seq=seq,
                session_id=session_id,
                source_kind=envelope.get("source_kind", "unknown"),
                event_name=event_name,
                payload=payload,
            )

            if row_id is None:
                # Duplicate — skip reducer
                return

            # Run through reducer
            self._reducer.handle(event_name, session_id, payload, received_at_ms)

            # Push to WebSocket clients
            self._broadcast_ws(
                {
                    "type": "event",
                    "event_name": event_name,
                    "session_id": session_id,
                    "received_at_ms": received_at_ms,
                }
            )

        except Exception:
            logger.exception("Error processing event")

    def _broadcast_ws(self, message: dict[str, Any]) -> None:
        """Broadcast a message to all WebSocket clients.

        Thread-safe: uses _ws_lock to snapshot clients and schedules sends
        on the stored asyncio loop rather than calling get_event_loop().
        """
        loop = self._ws_loop
        if loop is None or not self._ws_clients:
            return
        data = json.dumps(message)
        with self._ws_lock:
            clients = list(self._ws_clients)
        for ws in clients:
            try:
                loop.call_soon_threadsafe(asyncio.ensure_future, ws.send(data))
            except Exception:
                with self._ws_lock:
                    self._ws_clients.discard(ws)

    # --- Unix socket listener ---

    def _run_socket_listener(self) -> None:
        """Listen on Unix domain socket for incoming events."""
        # Clean up stale socket
        if self._socket_path.exists():
            self._socket_path.unlink()
        self._socket_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self._socket_path))
        sock.listen(32)
        sock.settimeout(1.0)  # Allow periodic checks for shutdown

        logger.info("Listening on %s", self._socket_path)

        while self._running:
            try:
                conn, _ = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._running:
                    logger.exception("Socket accept error")
                break

            # Handle connection in bounded thread pool
            self._connection_pool.submit(self._handle_connection, conn)

        sock.close()
        # Clean up socket file
        try:
            self._socket_path.unlink(missing_ok=True)
        except Exception:
            pass

    def _handle_connection(self, conn: socket.socket) -> None:
        """Handle a single client connection."""
        try:
            conn.settimeout(5.0)
            data = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
                # Process complete lines
                while b"\n" in data:
                    line, data = data.split(b"\n", 1)
                    if line.strip():
                        try:
                            envelope = json.loads(line)
                            self.process_event(envelope)
                        except json.JSONDecodeError:
                            logger.debug("Invalid JSON from client")
        except TimeoutError:
            pass
        except Exception:
            logger.debug("Connection handler error", exc_info=True)
        finally:
            conn.close()

    # --- WebSocket server ---

    async def _ws_handler(self, websocket: Any) -> None:
        """Handle a WebSocket client connection."""
        with self._ws_lock:
            self._ws_clients.add(websocket)
            count = len(self._ws_clients)
        logger.info("WebSocket client connected (%d total)", count)
        try:
            async for message in websocket:
                # Handle client requests (e.g., query state)
                try:
                    request = json.loads(message)
                    response = self._handle_ws_request(request)
                    await websocket.send(json.dumps(response))
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"error": "invalid JSON"}))
        except Exception:
            pass
        finally:
            with self._ws_lock:
                self._ws_clients.discard(websocket)
                remaining = len(self._ws_clients)
            logger.info("WebSocket client disconnected (%d remaining)", remaining)

    def _handle_ws_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Handle a WebSocket query request."""
        req_type = request.get("type", "")

        if req_type == "sessions":
            return {"type": "sessions", "data": self._store.get_sessions()}

        elif req_type == "session_summary":
            session_id = request.get("session_id", "")
            return {
                "type": "session_summary",
                "data": self._store.get_session_summary(session_id),
            }

        elif req_type == "agents":
            session_id = request.get("session_id", "")
            return {"type": "agents", "data": self._store.get_agents(session_id)}

        elif req_type == "tool_calls":
            session_id = request.get("session_id", "")
            return {
                "type": "tool_calls",
                "data": self._store.get_recent_tool_calls(session_id),
            }

        elif req_type == "files":
            session_id = request.get("session_id", "")
            return {"type": "files", "data": self._store.get_files(session_id)}

        elif req_type == "alerts":
            session_id = request.get("session_id", "")
            return {"type": "alerts", "data": self._store.get_active_alerts(session_id)}

        elif req_type == "timeline":
            session_id = request.get("session_id", "")
            limit = request.get("limit", 200)
            event_filter = request.get("filter")
            return {
                "type": "timeline",
                "data": self._store.get_timeline(
                    session_id, limit=limit, event_filter=event_filter
                ),
            }

        elif req_type == "instructions":
            session_id = request.get("session_id", "")
            return {
                "type": "instructions",
                "data": self._store.get_instructions(session_id),
            }

        elif req_type == "tasks":
            session_id = request.get("session_id", "")
            return {"type": "tasks", "data": self._store.get_tasks(session_id)}

        elif req_type == "activities":
            session_id = request.get("session_id", "")
            agent_pk = request.get("agent_pk")
            limit = request.get("limit", 100)
            return {
                "type": "activities",
                "data": self._store.get_activities(
                    session_id, agent_pk=agent_pk, limit=limit
                ),
            }

        return {"error": f"unknown request type: {req_type}"}

    # --- Health checks ---

    def _run_health_checks(self) -> None:
        """Periodically check for stuck/orphaned agents."""
        while self._running:
            time.sleep(HEALTH_CHECK_INTERVAL)
            if not self._running:
                break
            try:
                for session in self._store.get_sessions(limit=10):
                    if session.get("ended_at_ms") is None:
                        self._reducer.check_stuck_and_orphaned(session["session_id"])
            except Exception:
                logger.debug("Health check error", exc_info=True)

    # --- Spool replay ---

    def _replay_spool(self) -> None:
        """Replay spooled events from when daemon was down.

        Uses hash-based dedup in insert_raw_event to handle partial replays
        after daemon crash — duplicate events are silently skipped.
        """
        events = read_spool_files()
        if not events:
            return
        logger.info("Replaying %d spooled events", len(events))
        replayed = 0
        for event in events:
            self.process_event(event)
            replayed += 1
        cleared = clear_spool()
        logger.info("Replayed %d events, cleared %d spool files", replayed, cleared)

    # --- Main run loop ---

    def _acquire_lock(self) -> bool:
        """Acquire a lock file to prevent dual daemon instances.

        Returns True if lock acquired, False if another daemon is running.
        """
        import fcntl

        self._lock_path = self._socket_path.with_suffix(".lock")
        try:
            self._lock_file = open(self._lock_path, "w")
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_file.write(str(os.getpid()))
            self._lock_file.flush()
            return True
        except OSError:
            logger.error(
                "Another daemon is already running (lock: %s)", self._lock_path
            )
            return False

    def _release_lock(self) -> None:
        """Release the daemon lock file."""
        import fcntl

        if hasattr(self, "_lock_file") and self._lock_file:
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()
                self._lock_path.unlink(missing_ok=True)
            except Exception:
                pass

    def run(self) -> int:
        """Run the daemon (blocking). Requires Unix (macOS/Linux)."""
        if sys.platform == "win32":
            logger.error(
                "cc-sidecar daemon requires Unix domain sockets (macOS/Linux). "
                "Windows is not supported."
            )
            return 1
        if not self._acquire_lock():
            return 1

        self._running = True
        logger.info(
            "cc-sidecar daemon starting (socket=%s, ws=%d)",
            self._socket_path,
            self._ws_port,
        )

        # Replay spooled events
        self._replay_spool()

        # Start socket listener in thread
        socket_thread = threading.Thread(target=self._run_socket_listener, daemon=True)
        socket_thread.start()

        # Start health check in thread
        health_thread = threading.Thread(target=self._run_health_checks, daemon=True)
        health_thread.start()

        # Run WebSocket server in main thread's event loop
        try:
            asyncio.run(self._run_ws_server())
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            self._running = False
            self._connection_pool.shutdown(wait=False)
            self._store.close()
            self._release_lock()
            logger.info("Daemon stopped")

        return 0

    async def _run_ws_server(self) -> None:
        """Run the WebSocket server."""
        # Store loop reference for cross-thread use by _broadcast_ws
        self._ws_loop = asyncio.get_running_loop()
        try:
            import websockets

            async with websockets.serve(self._ws_handler, "127.0.0.1", self._ws_port):
                logger.info("WebSocket server on ws://127.0.0.1:%d", self._ws_port)
                loop = asyncio.get_running_loop()
                stop = loop.create_future()

                def handle_signal():
                    if not stop.done():
                        stop.set_result(None)

                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        loop.add_signal_handler(sig, handle_signal)
                    except NotImplementedError:
                        pass  # Windows

                await stop
        except ImportError:
            logger.warning("websockets not installed — WebSocket server disabled")
            # Just keep running for socket listener
            while self._running:
                await asyncio.sleep(1)


def run_daemon(
    socket_path: str | None = None,
    ws_port: int = DEFAULT_WS_PORT,
    db_path: str | None = None,
) -> int:
    """Entry point for cc-sidecar daemon."""
    # Ensure at least INFO for daemon (CLI may have set DEBUG already)
    root = logging.getLogger()
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)
        if not root.handlers:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
            )
    daemon = SidecarDaemon(
        socket_path=socket_path,
        ws_port=ws_port,
        db_path=db_path,
    )
    return daemon.run()
