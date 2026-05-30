"""CLI entry point for cc-sidecar.

Subcommands:
    emit        Ingest a hook or statusline event (called by Claude Code hooks)
    statusline  Output statusline JSON for Claude Code statusLine config
    daemon      Run the long-lived sidecar daemon
    status      Check daemon status, DB info, and recent activity
    tui         Launch the terminal dashboard
    install     Install hooks into Claude Code settings
    uninstall   Remove hooks from Claude Code settings
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys

from . import __version__


def _setup_logging(debug: bool = False) -> None:
    """Configure logging for the CLI."""
    level = logging.DEBUG if debug else logging.WARNING
    log_format = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    logging.basicConfig(level=level, format=log_format, force=True)


def _run_status() -> int:
    """Check daemon status, DB info, and recent sessions."""
    from .db.store import DEFAULT_DB_PATH, EventStore
    from .ingest.transport import get_socket_path, get_spool_dir

    sock_path = get_socket_path()
    spool_dir = get_spool_dir()
    db_path = DEFAULT_DB_PATH

    # Check daemon reachability
    daemon_running = False
    daemon_pid = None
    if sock_path.exists():
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(str(sock_path))
            s.close()
            daemon_running = True
        except (ConnectionRefusedError, OSError):
            pass

    # Try to read PID from lock file
    lock_path = sock_path.with_suffix(".lock")
    if lock_path.exists():
        try:
            daemon_pid = lock_path.read_text().strip()
        except OSError:
            pass

    # Status line
    if daemon_running:
        pid_str = f" (pid {daemon_pid})" if daemon_pid else ""
        print(f"  daemon:  \033[32mrunning\033[0m{pid_str}")
    else:
        print("  daemon:  \033[31moffline\033[0m")

    # Socket
    print(f"  socket:  {sock_path}")

    # Spool
    spool_files = list(spool_dir.glob("*.jsonl")) if spool_dir.exists() else []
    spool_bytes = sum(f.stat().st_size for f in spool_files)
    if spool_files:
        print(f"  spool:   {len(spool_files)} file(s), {spool_bytes / 1024:.0f} KB")
    else:
        print("  spool:   empty")

    # DB
    if db_path.exists():
        db_size = db_path.stat().st_size
        if db_size > 1024 * 1024:
            size_str = f"{db_size / (1024 * 1024):.1f} MB"
        else:
            size_str = f"{db_size / 1024:.0f} KB"
        print(f"  db:      {db_path} ({size_str})")

        # Recent sessions
        try:
            store = EventStore(db_path)
            rows = store.get_sessions(limit=5)
            store.close()
            if rows:
                print(f"\n  Recent sessions ({len(rows)}):")
                for row in rows:
                    sid = (row.get("session_id") or "?")[:12]
                    model = row.get("model") or "unknown"
                    status = row.get("source") or "active"
                    started = row.get("started_at_ms") or ""
                    print(f"    {sid}  {model:<30s}  {status:<10s}  {started}")
        except Exception:
            pass  # DB may be locked or schema mismatch
    else:
        print("  db:      not created yet")

    return 0 if daemon_running else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cc-sidecar",
        description="Passive observability sidecar for Claude Code",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"cc-sidecar {__version__}",
    )
    parser.add_argument(
        "--debug",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )
    sub = parser.add_subparsers(dest="command")

    # emit
    emit_p = sub.add_parser("emit", help="Ingest a hook/statusline event")
    emit_p.add_argument(
        "--subagent",
        action="store_true",
        help="Mark this event as originating from a subagent with frontmatter hooks",
    )
    emit_p.add_argument(
        "--event-name",
        default=None,
        help="Override event name (auto-detected from stdin JSON when omitted)",
    )

    # statusline
    sub.add_parser("statusline", help="Statusline script for Claude Code")

    # daemon
    daemon_p = sub.add_parser("daemon", help="Run the sidecar daemon")
    daemon_p.add_argument("--socket", default=None, help="Unix socket path")
    daemon_p.add_argument(
        "--port", type=int, default=9340, help="WebSocket port for UIs"
    )
    daemon_p.add_argument("--db", default=None, help="SQLite database path")

    # status
    sub.add_parser("status", help="Check daemon status and recent activity")

    # tui
    sub.add_parser("tui", help="Launch the terminal dashboard")

    # install
    install_p = sub.add_parser(
        "install", help="Install hooks into Claude Code settings"
    )
    install_p.add_argument(
        "--scope",
        choices=["user", "project", "local"],
        default="project",
        help="Settings scope to install into",
    )
    install_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print config without writing",
    )

    # uninstall
    uninstall_p = sub.add_parser(
        "uninstall", help="Remove hooks from Claude Code settings"
    )
    uninstall_p.add_argument(
        "--scope",
        choices=["user", "project", "local"],
        default="project",
        help="Settings scope to uninstall from",
    )

    args = parser.parse_args(argv)
    _setup_logging(debug=getattr(args, "debug", False))

    if args.command == "emit":
        from cc_sidecar.ingest.emit import run_emit

        return run_emit(subagent=args.subagent, event_name_override=args.event_name)

    elif args.command == "statusline":
        from cc_sidecar.ingest.statusline import run_statusline

        return run_statusline()

    elif args.command == "daemon":
        from cc_sidecar.daemon.server import run_daemon

        return run_daemon(
            socket_path=args.socket,
            ws_port=args.port,
            db_path=args.db,
        )

    elif args.command == "status":
        return _run_status()

    elif args.command == "tui":
        from cc_sidecar.tui.app import run_tui

        return run_tui()

    elif args.command == "install":
        from cc_sidecar.config.install import run_install

        return run_install(scope=args.scope, dry_run=args.dry_run)

    elif args.command == "uninstall":
        from cc_sidecar.config.install import run_uninstall

        return run_uninstall(scope=args.scope)

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
