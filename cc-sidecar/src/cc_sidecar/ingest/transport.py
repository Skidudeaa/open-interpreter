"""
Transport — local socket/spool transport to sidecar daemon.

Design:
    - Primary: Unix domain socket to the daemon
    - Fallback: append to spool file for later replay
    - Non-blocking, fire-and-forget
    - Never blocks the calling process (hooks must be fast)
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_SOCKET_DIR = Path.home() / ".cc-sidecar"
DEFAULT_SOCKET_PATH = DEFAULT_SOCKET_DIR / "daemon.sock"
DEFAULT_SPOOL_DIR = DEFAULT_SOCKET_DIR / "spool"

# Protocol: each message is a single JSON line terminated by newline
NEWLINE = b"\n"

# Socket timeout for non-blocking sends
SEND_TIMEOUT = 0.5  # seconds

# Maximum spool size before dropping events (50 MB)
# WHY: On long daemon outages, spool can grow unbounded.
# After this limit, new events are silently dropped to prevent disk exhaustion.
MAX_SPOOL_BYTES = 50 * 1024 * 1024


def get_socket_path() -> Path:
    """Get the daemon socket path, respecting env override."""
    env_path = os.environ.get("CC_SIDECAR_SOCKET")
    if env_path:
        return Path(env_path)
    return DEFAULT_SOCKET_PATH


def get_spool_dir() -> Path:
    """Get the spool directory for offline buffering."""
    env_path = os.environ.get("CC_SIDECAR_SPOOL_DIR")
    if env_path:
        return Path(env_path)
    return DEFAULT_SPOOL_DIR


def send_event(event: dict[str, Any]) -> bool:
    """Send an event to the daemon. Falls back to spool on failure.

    Returns True if sent to daemon, False if spooled.
    Never raises — hooks must not fail.
    """
    try:
        return _send_socket(event)
    except Exception:
        try:
            _spool_event(event)
            logger.debug("Event spooled (daemon unreachable)")
        except Exception:
            logger.debug("Event dropped — spool write failed", exc_info=True)
        return False


def _send_socket(event: dict[str, Any]) -> bool:
    """Send event via Unix domain socket."""
    sock_path = get_socket_path()
    if not sock_path.exists():
        raise ConnectionError("Daemon socket not found")

    data = json.dumps(event, separators=(",", ":")).encode("utf-8") + NEWLINE
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(SEND_TIMEOUT)
    try:
        sock.connect(str(sock_path))
        sock.sendall(data)
        return True
    finally:
        sock.close()


def _spool_event(event: dict[str, Any]) -> None:
    """Append event to spool file for later replay.

    Respects MAX_SPOOL_BYTES to prevent disk exhaustion on long outages.
    """
    spool_dir = get_spool_dir()
    spool_dir.mkdir(parents=True, exist_ok=True)

    # Check total spool size before writing
    total_size = sum(f.stat().st_size for f in spool_dir.glob("events_*.jsonl"))
    if total_size >= MAX_SPOOL_BYTES:
        logger.debug("Spool size limit reached (%d bytes), dropping event", total_size)
        return

    # One spool file per hour to keep file count manageable
    hour_key = time.strftime("%Y%m%d_%H", time.gmtime())
    spool_file = spool_dir / f"events_{hour_key}.jsonl"

    line = json.dumps(event, separators=(",", ":")) + "\n"
    with open(spool_file, "a") as f:
        f.write(line)


def read_spool_files() -> list[dict[str, Any]]:
    """Read all spooled events, sorted by timestamp. Used by daemon on startup."""
    spool_dir = get_spool_dir()
    if not spool_dir.exists():
        return []

    events = []
    for spool_file in sorted(spool_dir.glob("events_*.jsonl")):
        try:
            with open(spool_file) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
        except (json.JSONDecodeError, OSError):
            continue

    return events


def clear_spool() -> int:
    """Remove all spool files. Returns count of files removed."""
    spool_dir = get_spool_dir()
    if not spool_dir.exists():
        return 0
    count = 0
    for spool_file in spool_dir.glob("events_*.jsonl"):
        try:
            spool_file.unlink()
            count += 1
        except OSError:
            pass
    return count
