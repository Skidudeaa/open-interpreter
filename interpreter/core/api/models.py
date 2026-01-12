"""
Pydantic models for the Open Interpreter API.

# ARCHITECTURE: Request/response models for REST API endpoints
# WHY: Type safety and automatic validation for iOS client communication
# TRADEOFF: Adds pydantic dependency, but provides clear contracts
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# --- Session Models ---


class SessionCreate(BaseModel):
    """Request to create a new session."""

    session_id: str | None = Field(
        default=None,
        description="Optional custom session ID. Auto-generated if not provided.",
    )
    model: str | None = Field(
        default=None, description="LLM model to use for this session"
    )
    auto_run: bool = Field(default=False, description="Auto-approve code execution")
    working_directory: str | None = Field(
        default=None, description="Working directory for file operations"
    )


class SessionInfo(BaseModel):
    """Information about an active session."""

    session_id: str
    created_at: datetime
    last_activity: datetime
    message_count: int
    model: str | None = None
    auto_run: bool = False
    is_active: bool = True


class SessionResponse(BaseModel):
    """Response after creating a session."""

    session_id: str
    created_at: datetime


class SessionList(BaseModel):
    """List of active sessions."""

    sessions: list[SessionInfo]
    total: int


# --- Agent Models ---


class AgentTemplate(BaseModel):
    """Available agent template."""

    name: str
    description: str
    role: str
    default_tools: list[str]


class AgentTemplateList(BaseModel):
    """List of available agent templates."""

    templates: list[AgentTemplate]


class AgentCreate(BaseModel):
    """Request to create an agent in a session."""

    template: str = Field(
        description="Agent template name: scout, surgeon, architect, etc."
    )
    name: str | None = Field(default=None, description="Custom agent name")
    system_prompt: str | None = Field(
        default=None, description="Override default system prompt"
    )


class AgentInfo(BaseModel):
    """Information about an active agent."""

    name: str
    template: str
    status: str
    created_at: datetime


class AgentTask(BaseModel):
    """Request to execute an agent task."""

    task: str = Field(description="Task description for the agent to execute")
    context: dict[str, Any] | None = Field(
        default=None, description="Additional context"
    )


class AgentResult(BaseModel):
    """Result of an agent task execution."""

    success: bool
    result: str | None = None
    error: str | None = None
    execution_time_ms: int


# --- Memory/History Models ---


class ChatMessage(BaseModel):
    """A single chat message."""

    role: str
    type: str | None = None
    content: str
    format: str | None = None
    timestamp: datetime | None = None


class ConversationHistory(BaseModel):
    """Conversation history for a session."""

    session_id: str
    messages: list[ChatMessage]
    total: int


class MemoryEdit(BaseModel):
    """A tracked file edit."""

    file_path: str
    edit_type: str
    timestamp: datetime
    summary: str | None = None


class MemoryEditList(BaseModel):
    """List of tracked edits."""

    edits: list[MemoryEdit]
    total: int


class MemorySearchResult(BaseModel):
    """Result of a semantic memory search."""

    query: str
    results: list[dict[str, Any]]
    total: int


# --- Computer Control Models ---


class CodeExecuteRequest(BaseModel):
    """Request to execute code."""

    language: str = Field(
        description="Programming language: python, javascript, bash, etc."
    )
    code: str = Field(description="Code to execute")


class CodeExecuteResponse(BaseModel):
    """Response from code execution."""

    output: str
    exit_code: int | None = None
    error: str | None = None


class TerminalRequest(BaseModel):
    """Request to run a terminal command."""

    command: str = Field(description="Shell command to execute")
    timeout: int = Field(default=30, description="Timeout in seconds")


class FileReadRequest(BaseModel):
    """Request to read a file."""

    path: str = Field(description="File path to read")
    encoding: str = Field(default="utf-8", description="File encoding")


class FileWriteRequest(BaseModel):
    """Request to write a file."""

    path: str = Field(description="File path to write")
    content: str = Field(description="Content to write")
    encoding: str = Field(default="utf-8", description="File encoding")


class FileSearchRequest(BaseModel):
    """Request to search for files."""

    pattern: str = Field(description="Search pattern (glob or regex)")
    path: str = Field(default=".", description="Base path to search from")
    recursive: bool = Field(default=True, description="Search recursively")


class FileInfo(BaseModel):
    """Information about a file."""

    path: str
    size: int
    modified: datetime
    is_directory: bool


class FileSearchResponse(BaseModel):
    """Response from file search."""

    files: list[FileInfo]
    total: int


# --- Health Models ---


class HealthResponse(BaseModel):
    """Detailed health check response."""

    status: str
    version: str | None = None
    features: dict[str, bool] = {}
    active_sessions: int = 0
    uptime_seconds: float = 0.0
    model: str | None = None
