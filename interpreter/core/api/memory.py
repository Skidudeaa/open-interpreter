"""
Memory and history API endpoints.

# ARCHITECTURE: REST endpoints for conversation history and semantic memory
# WHY: iOS clients need to retrieve/clear history and query semantic edits
# TRADEOFF: Exposes conversation data; scope queries to session for security
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from .models import (
    ChatMessage,
    ConversationHistory,
    MemoryEdit,
    MemoryEditList,
    MemorySearchResult,
)
from .sessions import session_manager

# Module logger for memory API debugging
logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory"])


@router.get("/sessions/{session_id}/history", response_model=ConversationHistory)
async def get_conversation_history(
    session_id: str,
    limit: int = Query(
        default=100, ge=1, le=1000, description="Max messages to return"
    ),
    offset: int = Query(default=0, ge=0, description="Offset for pagination"),
):
    """
    Get conversation history for a session.

    Returns the messages exchanged in the session.
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    interpreter = session.interpreter
    all_messages = interpreter.messages

    # Apply pagination
    total = len(all_messages)
    paginated = all_messages[offset : offset + limit]

    # Convert to ChatMessage format
    chat_messages = []
    for msg in paginated:
        chat_messages.append(
            ChatMessage(
                role=msg.get("role", "unknown"),
                type=msg.get("type"),
                content=str(msg.get("content", "")),
                format=msg.get("format"),
                timestamp=None,  # Original messages don't have timestamps
            )
        )

    return ConversationHistory(
        session_id=session_id,
        messages=chat_messages,
        total=total,
    )


@router.delete("/sessions/{session_id}/history")
async def clear_conversation_history(session_id: str):
    """
    Clear conversation history for a session.

    This resets the session's message list to empty.
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    interpreter = session.interpreter
    message_count = len(interpreter.messages)
    interpreter.messages = []

    return {"status": "cleared", "messages_removed": message_count}


@router.get("/sessions/{session_id}/memory/edits", response_model=MemoryEditList)
async def get_memory_edits(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=500, description="Max edits to return"),
):
    """
    Get tracked file edits from semantic memory.

    Returns recent file modifications tracked by the SemanticEditGraph.
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    interpreter = session.interpreter

    # Check if semantic memory is enabled and available
    if not getattr(interpreter, "enable_semantic_memory", False):
        return MemoryEditList(edits=[], total=0)

    # Try to access the semantic graph
    semantic_graph = getattr(interpreter, "_semantic_graph", None)
    if semantic_graph is None:
        # Try lazy loading
        try:
            semantic_graph = interpreter.semantic_graph
        except Exception as e:
            logger.debug(f"Failed to access semantic graph: {e}")
            return MemoryEditList(edits=[], total=0)

    # Query recent edits from the semantic graph
    edits = []
    try:
        # Get recent edits - the semantic graph should have a method for this
        if hasattr(semantic_graph, "get_recent_edits"):
            raw_edits = semantic_graph.get_recent_edits(limit=limit)
            for edit in raw_edits:
                edits.append(
                    MemoryEdit(
                        file_path=edit.get("file_path", "unknown"),
                        edit_type=edit.get("edit_type", "unknown"),
                        timestamp=edit.get("timestamp", datetime.now()),
                        summary=edit.get("summary"),
                    )
                )
        elif hasattr(semantic_graph, "query"):
            # Fallback to general query
            results = semantic_graph.query(
                "SELECT file_path, edit_type, timestamp, summary FROM edits ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            for row in results:
                edits.append(
                    MemoryEdit(
                        file_path=row[0],
                        edit_type=row[1],
                        timestamp=(
                            datetime.fromisoformat(row[2]) if row[2] else datetime.now()
                        ),
                        summary=row[3],
                    )
                )
    except Exception as e:
        # If querying fails, return empty list
        logger.debug(f"Failed to query semantic graph edits: {e}")

    return MemoryEditList(edits=edits, total=len(edits))


@router.get("/sessions/{session_id}/memory/search", response_model=MemorySearchResult)
async def search_memory(
    session_id: str,
    q: str = Query(description="Search query for semantic memory"),
    limit: int = Query(default=10, ge=1, le=100, description="Max results to return"),
):
    """
    Search semantic memory for relevant content.

    Performs a semantic search across tracked symbols, edits, and conversation.
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    interpreter = session.interpreter

    # Check if semantic memory is enabled
    if not getattr(interpreter, "enable_semantic_memory", False):
        return MemorySearchResult(query=q, results=[], total=0)

    # Try to access the semantic graph
    semantic_graph = getattr(interpreter, "_semantic_graph", None)
    if semantic_graph is None:
        try:
            semantic_graph = interpreter.semantic_graph
        except Exception as e:
            logger.debug(f"Failed to access semantic graph for search: {e}")
            return MemorySearchResult(query=q, results=[], total=0)

    # Perform semantic search
    results = []
    try:
        if hasattr(semantic_graph, "semantic_search"):
            # Pass the interpreter's configured reranker (None when disabled);
            # semantic_search falls back to recency order without it.
            raw_results = semantic_graph.semantic_search(
                q, limit=limit, reranker=getattr(interpreter, "reranker", None)
            )
            results = [
                {
                    "type": r.get("type", "unknown"),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0.0),
                    "metadata": r.get("metadata", {}),
                }
                for r in raw_results
            ]
        elif hasattr(semantic_graph, "search"):
            raw_results = semantic_graph.search(q, limit=limit)
            results = [{"content": str(r)} for r in raw_results]
    except Exception as e:
        logger.debug(f"Semantic search failed: {e}")

    return MemorySearchResult(query=q, results=results, total=len(results))
