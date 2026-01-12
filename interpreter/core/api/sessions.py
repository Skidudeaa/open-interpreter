"""
Session management for multi-client support.

# ARCHITECTURE: SessionManager maintains isolated AsyncInterpreter instances per client
# WHY: Each iOS client needs its own conversation state, message history, and output queue
# TRADEOFF: Memory usage scales with active sessions, but provides true isolation
# NOTE: Sessions auto-expire after INTERPRETER_SESSION_TIMEOUT seconds of inactivity
"""

import os
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

import shortuuid

if TYPE_CHECKING:
    from ..async_core import AsyncInterpreter

# Lazy import to avoid circular dependencies
_AsyncInterpreter = None


def _get_async_interpreter_class():
    """Lazy load AsyncInterpreter to avoid circular imports."""
    global _AsyncInterpreter
    if _AsyncInterpreter is None:
        from ..async_core import AsyncInterpreter

        _AsyncInterpreter = AsyncInterpreter
    return _AsyncInterpreter


class Session:
    """
    Wrapper around an AsyncInterpreter with metadata.

    # ARCHITECTURE: Encapsulates interpreter + session lifecycle state
    # WHY: Need to track activity, creation time, and message count for management
    """

    def __init__(
        self,
        session_id: str,
        interpreter: "AsyncInterpreter",
        model: str | None = None,
        auto_run: bool = False,
    ):
        self.session_id = session_id
        self.interpreter = interpreter
        self.created_at = datetime.now()
        self.last_activity = datetime.now()
        self.model = model
        self.auto_run = auto_run
        self._lock = threading.Lock()

    def touch(self) -> None:
        """Update last activity timestamp."""
        with self._lock:
            self.last_activity = datetime.now()

    @property
    def message_count(self) -> int:
        """Get number of messages in conversation."""
        return len(self.interpreter.messages)

    @property
    def is_active(self) -> bool:
        """Check if session has an active response thread."""
        thread = self.interpreter.respond_thread
        return thread is not None and thread.is_alive()

    def to_dict(self) -> dict:
        """Convert to dictionary for API response."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "message_count": self.message_count,
            "model": self.model,
            "auto_run": self.auto_run,
            "is_active": self.is_active,
        }


class SessionManager:
    """
    Manages isolated AsyncInterpreter instances per client.

    # ARCHITECTURE: Thread-safe session registry with automatic cleanup
    # WHY: Multiple iOS clients need independent conversation states
    # TRADEOFF: Each session consumes memory for interpreter state
    # NOTE: Background thread cleans up stale sessions based on timeout
    """

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.RLock()
        self._timeout = int(os.getenv("INTERPRETER_SESSION_TIMEOUT", "3600"))
        self._cleanup_interval = 60  # Check every 60 seconds
        self._cleanup_thread: threading.Thread | None = None
        self._stop_cleanup = threading.Event()
        self._started = False

    def start(self) -> None:
        """Start the background cleanup thread."""
        if self._started:
            return
        self._started = True
        self._stop_cleanup.clear()
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def stop(self) -> None:
        """Stop the background cleanup thread."""
        self._stop_cleanup.set()
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)
        self._started = False

    def _cleanup_loop(self) -> None:
        """Background loop to clean up stale sessions."""
        while not self._stop_cleanup.is_set():
            self.cleanup_stale()
            # Wait in small increments to allow quick shutdown
            for _ in range(self._cleanup_interval):
                if self._stop_cleanup.is_set():
                    break
                time.sleep(1)

    def create(
        self,
        session_id: str | None = None,
        model: str | None = None,
        auto_run: bool = False,
        working_directory: str | None = None,
    ) -> str:
        """
        Create a new session with an isolated interpreter.

        Args:
            session_id: Optional custom session ID. Auto-generated if not provided.
            model: LLM model to use for this session.
            auto_run: Whether to auto-approve code execution.
            working_directory: Working directory for file operations.

        Returns:
            The session ID (generated or provided).
        """
        if session_id is None:
            session_id = shortuuid.uuid()

        with self._lock:
            if session_id in self._sessions:
                # Session already exists, return it
                return session_id

            # Create a new AsyncInterpreter for this session
            AsyncInterpreter = _get_async_interpreter_class()
            interpreter = AsyncInterpreter()

            # Configure the interpreter
            if model:
                interpreter.llm.model = model
            interpreter.auto_run = auto_run
            if working_directory:
                interpreter.computer.working_directory = working_directory

            # Create session wrapper
            session = Session(
                session_id=session_id,
                interpreter=interpreter,
                model=model,
                auto_run=auto_run,
            )
            self._sessions[session_id] = session

            # Start cleanup thread if not already running
            self.start()

            return session_id

    def get(self, session_id: str) -> "AsyncInterpreter | None":
        """
        Get the interpreter for a session.

        Args:
            session_id: The session ID.

        Returns:
            The AsyncInterpreter instance, or None if session doesn't exist.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.touch()
                return session.interpreter
            return None

    def get_session(self, session_id: str) -> Session | None:
        """
        Get the session wrapper.

        Args:
            session_id: The session ID.

        Returns:
            The Session instance, or None if session doesn't exist.
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.touch()
            return session

    def destroy(self, session_id: str) -> bool:
        """
        Destroy a session and cleanup resources.

        Args:
            session_id: The session ID to destroy.

        Returns:
            True if session was destroyed, False if it didn't exist.
        """
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                # Stop any active response thread
                interpreter = session.interpreter
                if interpreter.respond_thread and interpreter.respond_thread.is_alive():
                    interpreter.stop_event.set()
                    interpreter.respond_thread.join(timeout=5)
                return True
            return False

    def list_sessions(self) -> list[dict]:
        """
        List all active sessions.

        Returns:
            List of session info dictionaries.
        """
        with self._lock:
            return [session.to_dict() for session in self._sessions.values()]

    def cleanup_stale(self) -> int:
        """
        Remove sessions that have been inactive beyond the timeout.

        Returns:
            Number of sessions removed.
        """
        now = datetime.now()
        stale_ids = []

        with self._lock:
            for session_id, session in self._sessions.items():
                inactive_seconds = (now - session.last_activity).total_seconds()
                if inactive_seconds > self._timeout:
                    stale_ids.append(session_id)

        # Destroy stale sessions outside the main lock
        count = 0
        for session_id in stale_ids:
            if self.destroy(session_id):
                count += 1

        return count

    @property
    def active_count(self) -> int:
        """Get the number of active sessions."""
        with self._lock:
            return len(self._sessions)


# Global session manager instance
session_manager = SessionManager()
