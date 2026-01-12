"""
Computer control API endpoints.

# ARCHITECTURE: REST endpoints for code execution, terminal, file operations
# WHY: Expose computer.run() and other capabilities to iOS clients
# TRADEOFF: HIGH SECURITY RISK - only enable via INTERPRETER_INSECURE_ROUTES
# NOTE: All endpoints require session context for isolation
"""

import base64
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .models import (
    CodeExecuteRequest,
    CodeExecuteResponse,
    FileInfo,
    FileReadRequest,
    FileSearchRequest,
    FileSearchResponse,
    FileWriteRequest,
    TerminalRequest,
)
from .sessions import session_manager

# Only create router if insecure routes are enabled
INSECURE_ROUTES_ENABLED = os.getenv("INTERPRETER_INSECURE_ROUTES", "").lower() == "true"

router = APIRouter(prefix="/computer", tags=["computer"])


def _require_insecure_routes():
    """Check that insecure routes are enabled."""
    if not INSECURE_ROUTES_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Computer control endpoints are disabled. "
            "Set INTERPRETER_INSECURE_ROUTES=true to enable (HIGH SECURITY RISK).",
        )


@router.post("/sessions/{session_id}/run", response_model=CodeExecuteResponse)
async def execute_code(session_id: str, request: CodeExecuteRequest):
    """
    Execute code in the session's interpreter.

    Runs code in the specified language and returns the output.
    WARNING: This executes arbitrary code. Only enable in trusted environments.
    """
    _require_insecure_routes()

    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    interpreter = session.interpreter

    try:
        output = interpreter.computer.run(request.language, request.code)
        return CodeExecuteResponse(
            output=str(output) if output else "",
            exit_code=0,
            error=None,
        )
    except Exception as e:
        return CodeExecuteResponse(
            output="",
            exit_code=1,
            error=str(e),
        )


@router.post("/sessions/{session_id}/terminal", response_model=CodeExecuteResponse)
async def run_terminal_command(session_id: str, request: TerminalRequest):
    """
    Run a terminal/shell command.

    Executes a bash command and returns the output.
    WARNING: This executes arbitrary shell commands. Only enable in trusted environments.
    """
    _require_insecure_routes()

    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    interpreter = session.interpreter

    try:
        # Use the terminal module
        if hasattr(interpreter.computer, "terminal"):
            output = interpreter.computer.terminal.run(
                "bash", request.command, timeout=request.timeout
            )
        else:
            # Fallback to run
            output = interpreter.computer.run("bash", request.command)

        return CodeExecuteResponse(
            output=str(output) if output else "",
            exit_code=0,
            error=None,
        )
    except Exception as e:
        return CodeExecuteResponse(
            output="",
            exit_code=1,
            error=str(e),
        )


@router.get("/sessions/{session_id}/screenshot")
async def capture_screenshot(session_id: str):
    """
    Capture a screenshot of the display.

    Returns the screenshot as base64-encoded image data.
    Requires OS mode to be enabled.
    """
    _require_insecure_routes()

    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    interpreter = session.interpreter

    try:
        # Check if display module is available
        if not hasattr(interpreter.computer, "display"):
            raise HTTPException(
                status_code=501, detail="Display module not available. Enable OS mode."
            )

        # Capture screenshot
        screenshot = interpreter.computer.display.screenshot()

        if screenshot is None:
            raise HTTPException(status_code=500, detail="Failed to capture screenshot")

        # Encode as base64
        if isinstance(screenshot, bytes):
            encoded = base64.b64encode(screenshot).decode("utf-8")
        else:
            # Might be a path or other format
            encoded = str(screenshot)

        return {
            "format": "base64.png",
            "data": encoded,
            "timestamp": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Screenshot failed: {str(e)}"
        ) from e


@router.post("/sessions/{session_id}/files/read")
async def read_file(session_id: str, request: FileReadRequest):
    """
    Read a file's contents.

    Returns the file content as text.
    """
    _require_insecure_routes()

    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    try:
        path = Path(request.path)
        if not path.exists():
            raise HTTPException(
                status_code=404, detail=f"File not found: {request.path}"
            )
        if not path.is_file():
            raise HTTPException(status_code=400, detail=f"Not a file: {request.path}")

        content = path.read_text(encoding=request.encoding)
        return {
            "path": str(path.absolute()),
            "content": content,
            "size": path.stat().st_size,
            "encoding": request.encoding,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Read failed: {str(e)}") from e


@router.post("/sessions/{session_id}/files/write")
async def write_file(session_id: str, request: FileWriteRequest):
    """
    Write content to a file.

    Creates or overwrites the file with the provided content.
    """
    _require_insecure_routes()

    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    try:
        path = Path(request.path)

        # Create parent directories if needed
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write the file
        path.write_text(request.content, encoding=request.encoding)

        return {
            "path": str(path.absolute()),
            "size": path.stat().st_size,
            "status": "written",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Write failed: {str(e)}") from e


@router.post("/sessions/{session_id}/files/search", response_model=FileSearchResponse)
async def search_files(session_id: str, request: FileSearchRequest):
    """
    Search for files matching a pattern.

    Uses glob pattern matching to find files.
    """
    _require_insecure_routes()

    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    try:
        base_path = Path(request.path)
        if not base_path.exists():
            raise HTTPException(
                status_code=404, detail=f"Path not found: {request.path}"
            )

        # Use glob to search
        if request.recursive:
            matches = list(base_path.rglob(request.pattern))
        else:
            matches = list(base_path.glob(request.pattern))

        # Convert to FileInfo
        files = []
        for match in matches[:100]:  # Limit to 100 results
            try:
                stat = match.stat()
                files.append(
                    FileInfo(
                        path=str(match.absolute()),
                        size=stat.st_size,
                        modified=datetime.fromtimestamp(stat.st_mtime),
                        is_directory=match.is_dir(),
                    )
                )
            except (OSError, PermissionError):
                continue  # Skip files we can't stat

        return FileSearchResponse(files=files, total=len(files))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}") from e
