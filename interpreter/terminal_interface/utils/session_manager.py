"""
Session Manager - Autosave and resume functionality.

Features:
- Automatic session save on interrupt
- Resume from previous session
- Session history management
"""

import atexit
import json
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Session storage directory
def get_sessions_dir() -> Path:
    """Get the sessions directory, creating it if needed."""
    sessions_dir = Path.home() / ".open-interpreter" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def get_session_path(session_id: str) -> Path:
    """Get path for a specific session file."""
    return get_sessions_dir() / f"{session_id}.json"


def generate_session_id() -> str:
    """Generate a unique session ID based on timestamp."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class SessionManager:
    """
    Manages session persistence and recovery.

    Usage:
        manager = SessionManager(interpreter)
        manager.enable_autosave()

        # Later...
        sessions = manager.list_sessions()
        manager.load_session(sessions[0])
    """

    def __init__(self, interpreter):
        self.interpreter = interpreter
        self.session_id: str = generate_session_id()
        self.autosave_enabled: bool = False
        self._original_sigint = None

    def enable_autosave(self):
        """Enable automatic session saving on interrupt/exit."""
        if self.autosave_enabled:
            return

        self.autosave_enabled = True

        # Register atexit handler
        atexit.register(self._autosave)

        # Register signal handlers
        self._original_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_interrupt)

    def disable_autosave(self):
        """Disable automatic session saving."""
        if not self.autosave_enabled:
            return

        self.autosave_enabled = False

        # Unregister atexit handler
        atexit.unregister(self._autosave)

        # Restore original signal handler
        if self._original_sigint:
            signal.signal(signal.SIGINT, self._original_sigint)

    def _handle_interrupt(self, signum, frame):
        """Handle SIGINT by saving session before exit."""
        self._autosave()
        if self._original_sigint:
            self._original_sigint(signum, frame)
        else:
            sys.exit(0)

    def _autosave(self):
        """Save current session state."""
        if not self.interpreter.messages:
            return  # Don't save empty sessions

        try:
            self.save_session()
        except Exception:
            pass  # Don't crash on autosave failure

    def save_session(self, session_id: str = None) -> str:
        """
        Save current session to file.

        Args:
            session_id: Optional custom session ID

        Returns:
            Session ID used
        """
        if session_id:
            self.session_id = session_id

        session_data = {
            "id": self.session_id,
            "created": datetime.now().isoformat(),
            "model": getattr(self.interpreter.llm, 'model', 'unknown'),
            "messages": self.interpreter.messages,
            "system_message": self.interpreter.system_message,
            "settings": {
                "auto_run": self.interpreter.auto_run,
                "safe_mode": self.interpreter.safe_mode,
                "os_mode": self.interpreter.os,
            }
        }

        session_path = get_session_path(self.session_id)
        with open(session_path, 'w') as f:
            json.dump(session_data, f, indent=2, default=str)

        return self.session_id

    def load_session(self, session_id: str) -> bool:
        """
        Load a saved session.

        Args:
            session_id: Session ID to load

        Returns:
            True if loaded successfully
        """
        session_path = get_session_path(session_id)

        if not session_path.exists():
            return False

        try:
            with open(session_path, 'r') as f:
                session_data = json.load(f)

            self.session_id = session_data["id"]
            self.interpreter.messages = session_data.get("messages", [])

            if "system_message" in session_data:
                self.interpreter.system_message = session_data["system_message"]

            settings = session_data.get("settings", {})
            if "auto_run" in settings:
                self.interpreter.auto_run = settings["auto_run"]
            if "safe_mode" in settings:
                self.interpreter.safe_mode = settings["safe_mode"]

            return True

        except (json.JSONDecodeError, KeyError):
            return False

    def list_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        List recent sessions.

        Args:
            limit: Maximum number of sessions to return

        Returns:
            List of session metadata dicts
        """
        sessions_dir = get_sessions_dir()
        sessions = []

        for session_file in sorted(sessions_dir.glob("*.json"), reverse=True)[:limit]:
            try:
                with open(session_file, 'r') as f:
                    data = json.load(f)
                    sessions.append({
                        "id": data.get("id", session_file.stem),
                        "created": data.get("created", "unknown"),
                        "model": data.get("model", "unknown"),
                        "message_count": len(data.get("messages", [])),
                    })
            except (json.JSONDecodeError, KeyError):
                pass

        return sessions

    def delete_session(self, session_id: str) -> bool:
        """Delete a saved session."""
        session_path = get_session_path(session_id)
        if session_path.exists():
            session_path.unlink()
            return True
        return False

    def clear_old_sessions(self, keep: int = 20):
        """Delete old sessions, keeping the most recent ones."""
        sessions_dir = get_sessions_dir()
        session_files = sorted(sessions_dir.glob("*.json"), reverse=True)

        for session_file in session_files[keep:]:
            try:
                session_file.unlink()
            except Exception:
                pass


def get_resume_prompt(interpreter) -> Optional[str]:
    """
    Check if there's a recent session to resume.

    Returns prompt message if resumable session exists.
    """
    manager = SessionManager(interpreter)
    sessions = manager.list_sessions(limit=1)

    if sessions:
        session = sessions[0]
        return (
            f"Found previous session from {session['created'][:16]} "
            f"with {session['message_count']} messages. Resume? (y/n)"
        )

    return None
