"""
Agent management API endpoints.

# ARCHITECTURE: REST endpoints for agent orchestration using SDK AgentBuilder
# WHY: Expose multi-agent capabilities (scout, surgeon, etc.) to iOS clients
# TRADEOFF: Agents execute code, so this has security implications
# NOTE: Uses existing SDK agent templates for consistency
"""

import asyncio
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from .models import (
    AgentCreate,
    AgentInfo,
    AgentResult,
    AgentTask,
    AgentTemplate,
    AgentTemplateList,
)
from .sessions import session_manager

router = APIRouter(prefix="/agents", tags=["agents"])

# Agent templates with descriptions
AGENT_TEMPLATES = {
    "scout": AgentTemplate(
        name="scout",
        description="Fast codebase exploration agent. Reads files, searches patterns, understands structure.",
        role="exploration",
        default_tools=["read", "grep", "glob", "ls"],
    ),
    "surgeon": AgentTemplate(
        name="surgeon",
        description="Precise code editing agent. Makes minimal, targeted changes to files.",
        role="editing",
        default_tools=["read", "edit", "write"],
    ),
    "architect": AgentTemplate(
        name="architect",
        description="Structural analysis agent. Understands architecture, AST, dependencies.",
        role="analysis",
        default_tools=["read", "grep", "glob"],
    ),
    "validator": AgentTemplate(
        name="validator",
        description="Testing and validation agent. Runs tests, checks syntax, validates changes.",
        role="validation",
        default_tools=["bash", "read"],
    ),
    "researcher": AgentTemplate(
        name="researcher",
        description="Information gathering agent. Searches web, documentation, external sources.",
        role="research",
        default_tools=["web_search", "read"],
    ),
}


# Track active agents per session
_session_agents: dict[str, dict[str, dict[str, Any]]] = {}


@router.get("/templates", response_model=AgentTemplateList)
async def list_agent_templates():
    """
    List available agent templates.

    Returns the built-in agent types that can be spawned in a session.
    """
    return AgentTemplateList(templates=list(AGENT_TEMPLATES.values()))


@router.post("/sessions/{session_id}/agents", response_model=AgentInfo)
async def create_agent(session_id: str, request: AgentCreate):
    """
    Create an agent in a session.

    The agent will use the session's interpreter for execution.
    """
    # Validate session exists
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    # Validate template
    template = AGENT_TEMPLATES.get(request.template)
    if not template:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown agent template: {request.template}. "
            f"Available: {list(AGENT_TEMPLATES.keys())}",
        )

    # Generate agent name if not provided
    agent_name = request.name or f"{request.template}_{int(time.time())}"

    # Initialize session agents dict if needed
    if session_id not in _session_agents:
        _session_agents[session_id] = {}

    # Check if agent already exists
    if agent_name in _session_agents[session_id]:
        raise HTTPException(
            status_code=409, detail=f"Agent {agent_name} already exists in session"
        )

    # Create agent entry
    agent_info = {
        "name": agent_name,
        "template": request.template,
        "status": "idle",
        "created_at": datetime.now(),
        "system_prompt": request.system_prompt,
    }
    _session_agents[session_id][agent_name] = agent_info

    return AgentInfo(
        name=agent_name,
        template=request.template,
        status="idle",
        created_at=agent_info["created_at"],
    )


@router.get("/sessions/{session_id}/agents", response_model=list[AgentInfo])
async def list_session_agents(session_id: str):
    """
    List agents in a session.
    """
    # Validate session exists
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    agents = _session_agents.get(session_id, {})
    return [
        AgentInfo(
            name=info["name"],
            template=info["template"],
            status=info["status"],
            created_at=info["created_at"],
        )
        for info in agents.values()
    ]


@router.post(
    "/sessions/{session_id}/agents/{agent_name}/execute", response_model=AgentResult
)
async def execute_agent_task(session_id: str, agent_name: str, request: AgentTask):
    """
    Execute a task with an agent.

    The agent will process the task and return the result.
    """
    # Validate session exists
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    # Validate agent exists
    agents = _session_agents.get(session_id, {})
    agent_info = agents.get(agent_name)
    if not agent_info:
        raise HTTPException(
            status_code=404,
            detail=f"Agent {agent_name} not found in session {session_id}",
        )

    # Get interpreter
    interpreter = session.interpreter

    # Build the task prompt based on agent type
    template = AGENT_TEMPLATES.get(agent_info["template"])
    system_context = (
        agent_info.get("system_prompt") or f"You are a {template.description}"
    )

    # Update agent status
    agent_info["status"] = "running"
    start_time = time.time()

    try:
        # Execute via the interpreter's chat
        # Prepend system context to the task
        full_prompt = f"[{agent_info['template'].upper()} AGENT]\n{system_context}\n\nTask: {request.task}"

        # Run in thread pool to avoid blocking
        result_content = ""

        def execute_chat():
            nonlocal result_content
            for chunk in interpreter.chat(
                message=full_prompt, stream=True, display=False
            ):
                if chunk.get("type") == "message" and "content" in chunk:
                    result_content += chunk["content"]

        # Run synchronously in thread
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, execute_chat)

        execution_time = int((time.time() - start_time) * 1000)
        agent_info["status"] = "idle"

        return AgentResult(
            success=True,
            result=result_content,
            error=None,
            execution_time_ms=execution_time,
        )

    except Exception as e:
        execution_time = int((time.time() - start_time) * 1000)
        agent_info["status"] = "error"

        return AgentResult(
            success=False,
            result=None,
            error=str(e),
            execution_time_ms=execution_time,
        )


@router.delete("/sessions/{session_id}/agents/{agent_name}")
async def delete_agent(session_id: str, agent_name: str):
    """
    Remove an agent from a session.
    """
    # Validate session exists
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    # Remove agent
    agents = _session_agents.get(session_id, {})
    if agent_name not in agents:
        raise HTTPException(
            status_code=404,
            detail=f"Agent {agent_name} not found in session {session_id}",
        )

    del agents[agent_name]
    return {"status": "deleted", "agent": agent_name}


def cleanup_session_agents(session_id: str) -> None:
    """
    Clean up agents when a session is destroyed.

    Called by session manager when destroying a session.
    """
    _session_agents.pop(session_id, None)
