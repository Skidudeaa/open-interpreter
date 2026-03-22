"""CLI entry point for cc-sidecar.

Subcommands:
    emit        Ingest a hook or statusline event (called by Claude Code hooks)
    statusline  Output statusline JSON for Claude Code statusLine config
    daemon      Run the long-lived sidecar daemon
    tui         Launch the terminal dashboard
    install     Install hooks into Claude Code settings
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cc-sidecar",
        description="Passive observability sidecar for Claude Code",
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
    daemon_p.add_argument("--port", type=int, default=9340, help="WebSocket port for UIs")
    daemon_p.add_argument("--db", default=None, help="SQLite database path")

    # tui
    sub.add_parser("tui", help="Launch the terminal dashboard")

    # install
    install_p = sub.add_parser("install", help="Install hooks into Claude Code settings")
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

    args = parser.parse_args(argv)

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

    elif args.command == "tui":
        from cc_sidecar.tui.app import run_tui

        return run_tui()

    elif args.command == "install":
        from cc_sidecar.config.install import run_install

        return run_install(scope=args.scope, dry_run=args.dry_run)

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
