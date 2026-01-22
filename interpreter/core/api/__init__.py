"""
Open Interpreter API module.

# ARCHITECTURE: Modular API with separate routers for each domain
# WHY: Clean separation of concerns, easy to enable/disable features
# TRADEOFF: Multiple files to maintain vs better organization

This module provides REST and WebSocket endpoints for:
- Session management (multi-client support)
- Agent orchestration (scout, surgeon, etc.)
- Memory and history access
- Computer control (code execution, files, terminal)

Usage:
    from interpreter.core.api import create_api_routers, session_manager

    # Add routers to FastAPI app
    for router in create_api_routers():
        app.include_router(router)
"""

import os
from typing import TYPE_CHECKING

from fastapi import APIRouter

from . import agents, computer, memory
from .models import (
    AgentCreate,
    AgentInfo,
    AgentResult,
    AgentTask,
    AgentTemplate,
    AgentTemplateList,
    ChatMessage,
    CodeExecuteRequest,
    CodeExecuteResponse,
    ConversationHistory,
    FileInfo,
    FileReadRequest,
    FileSearchRequest,
    FileSearchResponse,
    FileWriteRequest,
    HealthResponse,
    MemoryEdit,
    MemoryEditList,
    MemorySearchResult,
    SessionCreate,
    SessionInfo,
    SessionList,
    SessionResponse,
    TerminalRequest,
)
from .sessions import Session, SessionManager, session_manager

if TYPE_CHECKING:
    from ..async_core import AsyncInterpreter

__all__ = [
    # Session management
    "SessionManager",
    "Session",
    "session_manager",
    # Models
    "SessionCreate",
    "SessionInfo",
    "SessionList",
    "SessionResponse",
    "AgentTemplate",
    "AgentTemplateList",
    "AgentCreate",
    "AgentInfo",
    "AgentTask",
    "AgentResult",
    "ChatMessage",
    "ConversationHistory",
    "MemoryEdit",
    "MemoryEditList",
    "MemorySearchResult",
    "CodeExecuteRequest",
    "CodeExecuteResponse",
    "TerminalRequest",
    "FileReadRequest",
    "FileWriteRequest",
    "FileSearchRequest",
    "FileSearchResponse",
    "FileInfo",
    "HealthResponse",
    # Router creation
    "create_api_routers",
    "create_session_router",
]


def create_session_router() -> APIRouter:
    """
    Create the session management router.

    Includes endpoints for session CRUD operations.
    """
    from datetime import datetime

    from fastapi import HTTPException

    router = APIRouter(prefix="/sessions", tags=["sessions"])

    @router.post("", response_model=SessionResponse)
    async def create_session(request: SessionCreate | None = None):
        """Create a new session."""
        request = request or SessionCreate()
        session_id = session_manager.create(
            session_id=request.session_id,
            model=request.model,
            auto_run=request.auto_run,
            working_directory=request.working_directory,
        )
        session = session_manager.get_session(session_id)
        return SessionResponse(
            session_id=session_id,
            created_at=session.created_at if session else datetime.now(),
        )

    @router.get("", response_model=SessionList)
    async def list_sessions():
        """List all active sessions."""
        sessions_data = session_manager.list_sessions()
        sessions = [
            SessionInfo(
                session_id=s["session_id"],
                created_at=datetime.fromisoformat(s["created_at"]),
                last_activity=datetime.fromisoformat(s["last_activity"]),
                message_count=s["message_count"],
                model=s.get("model"),
                auto_run=s.get("auto_run", False),
                is_active=s.get("is_active", False),
            )
            for s in sessions_data
        ]
        return SessionList(sessions=sessions, total=len(sessions))

    @router.get("/{session_id}", response_model=SessionInfo)
    async def get_session(session_id: str):
        """Get information about a specific session."""
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(
                status_code=404, detail=f"Session {session_id} not found"
            )

        return SessionInfo(
            session_id=session.session_id,
            created_at=session.created_at,
            last_activity=session.last_activity,
            message_count=session.message_count,
            model=session.model,
            auto_run=session.auto_run,
            is_active=session.is_active,
        )

    @router.delete("/{session_id}")
    async def delete_session(session_id: str):
        """Delete a session and cleanup resources."""
        # Cleanup agents first
        agents.cleanup_session_agents(session_id)

        if not session_manager.destroy(session_id):
            raise HTTPException(
                status_code=404, detail=f"Session {session_id} not found"
            )

        return {"status": "deleted", "session_id": session_id}

    return router


def _get_package_version() -> str:
    """Get package version from metadata, with fallback."""
    try:
        from importlib.metadata import version

        return version("open-interpreter")
    except Exception:
        return "unknown"


def create_health_router(start_time: float | None = None) -> APIRouter:
    """
    Create the health/info router.

    Includes detailed health check endpoint.
    """
    import time

    _start_time = start_time or time.time()

    router = APIRouter(tags=["health"])

    @router.get("/health", response_model=HealthResponse)
    async def health_check():
        """Detailed health check with feature status."""
        return HealthResponse(
            status="healthy",
            version=_get_package_version(),
            features={
                "sessions": True,
                "agents": True,
                "memory": True,
                "computer_control": os.getenv("INTERPRETER_INSECURE_ROUTES", "").lower()
                == "true",
            },
            active_sessions=session_manager.active_count,
            uptime_seconds=time.time() - _start_time,
            model=None,  # Could be populated from default interpreter
        )

    return router


def create_api_routers() -> list[APIRouter]:
    """
    Create all API routers.

    Returns a list of routers to be included in the FastAPI app.
    """
    routers = [
        create_session_router(),
        create_health_router(),
        agents.router,
        memory.router,
    ]

    # Only include computer router if insecure routes are enabled
    if os.getenv("INTERPRETER_INSECURE_ROUTES", "").lower() == "true":
        routers.append(computer.router)

    return routers
