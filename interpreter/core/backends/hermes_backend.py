"""Hermes execution backend — drives NousResearch hermes-agent over ACP.

Public entry point: ``run(interpreter)``, a **synchronous generator** yielding LMC
chunk dicts, drop-in compatible with the OI core ``respond()`` generator. It is
selected at ``interpreter/core/core.py`` when ``interpreter.backend == "hermes"``.

Why a thread bridge: ACP is async JSON-RPC over a subprocess's stdio, but the
fork's ``_respond_and_store`` consumes a *synchronous* generator. So the asyncio
ACP client runs in a background thread that pushes translated chunks onto a
``queue.Queue``; the sync ``run()`` generator drains the queue and yields.

Permission handshake: a server→client ``session/request_permission`` arrives on
the async thread. Its handler enqueues a control item carrying a
``concurrent.futures.Future`` and a ready-made LMC ``confirmation`` chunk, then
awaits the future. The sync generator yields the confirmation chunk — which
suspends it until the existing UI sets ``interpreter._code_execution_approved``
(identical to the native flow in ``respond.py``) — then reads/clears that sentinel
and resolves the future, sending the approve/deny outcome back over ACP.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import queue
import threading
from typing import Any, Callable

from .acp_client import ACPClient, ACPError, HermesNotInstalled

# Sentinel pushed onto the queue when the background driver is finished.
_DONE = object()

# Isolated launch: `uvx --from "hermes-agent[acp]==0.16.0" hermes-acp`.
# The package is `hermes-agent` but the entry-point command is `hermes-acp`,
# hence --from. This runs in uvx's own environment — zero dep conflict with the fork.
HERMES_PACKAGE = "hermes-agent[acp]==0.16.0"
LAUNCH_CMD = ["uvx", "--from", HERMES_PACKAGE, "hermes-acp"]

# ACP auto-denies a permission request after 60s; resolve a touch under that.
PERMISSION_TIMEOUT = 55.0


# ---------------------------------------------------------------- translation


def _extract_text_from_content(content: list | None) -> str:
    """Flatten an ACP tool-call ``content[]`` union into display text."""
    parts: list[str] = []
    for item in content or []:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "content":
            inner = item.get("content") or {}
            if isinstance(inner, dict) and inner.get("type") == "text":
                text = inner.get("text")
                if text:
                    parts.append(text)
        elif itype == "diff":
            path = item.get("path", "")
            old = item.get("oldText") or ""
            new = item.get("newText") or ""
            parts.append(f"--- {path}\n{old}\n+++ {path}\n{new}".rstrip())
        # "terminal" content carries only a terminalId — nothing to surface.
    return "\n".join(p for p in parts if p)


def _translate_update(params: dict, emit: Callable[[dict], None]) -> None:
    """Map one ``session/update`` notification to zero or more LMC chunks."""
    update = params.get("update") or {}
    variant = update.get("sessionUpdate")

    if variant == "agent_message_chunk":
        content = update.get("content") or {}
        if isinstance(content, dict) and content.get("type") == "text":
            text = content.get("text")
            if text:
                emit({"role": "assistant", "type": "message", "content": text})

    elif variant == "tool_call":
        kind = update.get("kind")
        text = _extract_text_from_content(update.get("content"))
        title = update.get("title") or ""
        if kind == "execute":
            emit(
                {
                    "role": "assistant",
                    "type": "code",
                    "format": "shell",
                    "content": text or title,
                }
            )
        elif kind == "edit":
            body = f"✏️  {title}\n{text}".rstrip()
            emit({"role": "assistant", "type": "message", "content": body})
        else:
            emit(
                {
                    "role": "assistant",
                    "type": "message",
                    "content": f"🔧 {title or kind or 'tool'}",
                }
            )

    elif variant == "tool_call_update":
        if update.get("status") in ("completed", "failed"):
            out = _extract_text_from_content(update.get("content"))
            if not out:
                raw = update.get("rawOutput")
                out = raw if isinstance(raw, str) else ""
            if out:
                emit(
                    {
                        "role": "computer",
                        "type": "console",
                        "format": "output",
                        "content": out,
                    }
                )
    # plan / usage_update / available_commands_update / session_info_update / etc.
    # are dropped in this slice.


def _confirmation_chunk_from_toolcall(tool_call: dict) -> dict:
    """Build the LMC confirmation chunk the fork's approval UI understands."""
    kind = tool_call.get("kind")
    text = _extract_text_from_content(tool_call.get("content")) or (
        tool_call.get("title") or ""
    )
    lang = "shell" if kind == "execute" else (kind or "text")
    return {
        "role": "computer",
        "type": "confirmation",
        "format": "execution",
        "content": {"type": "code", "format": lang, "content": text},
    }


def _build_outcome(approved: bool, options: list) -> dict:
    """Pick an offered permission option by kind and form the ACP outcome reply."""
    wanted = (
        ("allow_once", "allow_always") if approved else ("reject_once", "reject_always")
    )
    chosen = None
    for opt in options or []:
        if isinstance(opt, dict) and opt.get("kind") in wanted:
            chosen = opt.get("optionId")
            break
    if chosen is None:
        if approved and options:
            chosen = options[0].get("optionId")
        else:
            # No matching option to express the choice — cancel the request.
            return {"outcome": {"outcome": "cancelled"}}
    return {"outcome": {"outcome": "selected", "optionId": chosen}}


# ---------------------------------------------------------------- helpers


def _latest_user_text(interpreter) -> str:
    for msg in reversed(getattr(interpreter, "messages", []) or []):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "user" and msg.get("type", "message") == "message":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(str(x) for x in content)
    return ""


def _map_model(oi_model: str, models: dict) -> str | None:
    """Best-effort map a litellm-style model id to a Hermes ``modelId``.

    Hermes model ids look like ``provider:model`` (e.g. ``openrouter:gpt-4o``);
    litellm ids look like ``gemini/gemini-3.5-flash``. Returns None to leave
    Hermes on its configured default.
    """
    available = [
        m.get("modelId")
        for m in (models.get("availableModels") or [])
        if isinstance(m, dict) and m.get("modelId")
    ]
    if not oi_model or not available:
        return None
    base = oi_model.split("/", 1)[1] if "/" in oi_model else oi_model
    for mid in available:  # exact match wins
        if mid == oi_model:
            return mid
    for mid in available:
        tail = mid.split(":", 1)[1] if ":" in mid else mid
        if tail == base or tail == oi_model or (base and base in tail):
            return mid
    return None


def _build_env(interpreter) -> dict:
    """Environment for the Hermes subprocess. Hermes resolves provider credentials
    itself from env / ~/.hermes/.env at session/new time, so we pass through."""
    return dict(os.environ)


def _install_hint_chunk() -> dict:
    return {
        "role": "assistant",
        "type": "message",
        "content": (
            "The Hermes backend needs `uvx` (from https://astral.sh/uv) and network "
            "access to fetch hermes-agent. Install uv, then retry — or run with "
            "`--backend oi` to use the built-in core."
        ),
    }


def _format_error(exc: Exception, client: ACPClient | None = None) -> str:
    msg = f"Hermes backend error: {exc}"
    if client is not None:
        tail = client.stderr_tail(10)
        if tail:
            msg += f"\n\n[hermes stderr]\n{tail}"
    return msg


def _read_approval(interpreter) -> bool:
    """Read and clear the UI-set approval sentinel (mirrors respond.py:951)."""
    approved = getattr(interpreter, "_code_execution_approved", True)
    try:
        delattr(interpreter, "_code_execution_approved")
    except AttributeError:
        pass
    return bool(approved)


def _make_permission_handler(
    chunk_q: queue.Queue,
) -> Callable[[dict], Any]:
    async def handler(params: dict) -> dict:
        tool_call = params.get("toolCall") or {}
        options = params.get("options") or []
        lmc_chunk = _confirmation_chunk_from_toolcall(tool_call)
        fut: concurrent.futures.Future = concurrent.futures.Future()
        chunk_q.put({"__permission__": True, "lmc_chunk": lmc_chunk, "future": fut})
        try:
            approved = await asyncio.wait_for(
                asyncio.wrap_future(fut), timeout=PERMISSION_TIMEOUT
            )
        except asyncio.TimeoutError:
            approved = False
        return _build_outcome(approved, options)

    return handler


# ---------------------------------------------------------------- driver


async def _watch_stop(stop_event, client: ACPClient, session_id: str) -> None:
    try:
        while True:
            if stop_event.is_set():
                await client.cancel(session_id)
                return
            await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        return


async def _drive(interpreter, chunk_q: queue.Queue, state: dict) -> None:
    """Background coroutine: own the subprocess, stream chunks onto the queue."""
    cwd = getattr(getattr(interpreter, "computer", None), "cwd", None) or os.getcwd()
    client = ACPClient(LAUNCH_CMD, cwd=cwd, env=_build_env(interpreter))
    state["client"] = client
    client.on_session_update = lambda p: _translate_update(p, chunk_q.put)
    client.on_permission_request = _make_permission_handler(chunk_q)

    try:
        await client.start()
    except (HermesNotInstalled, FileNotFoundError):
        chunk_q.put(_install_hint_chunk())
        return

    watcher: asyncio.Task | None = None
    try:
        await client.initialize()
        result = await client.new_session(cwd)
        session_id = result.get("sessionId")
        state["session_id"] = session_id

        model_id = _map_model(
            getattr(getattr(interpreter, "llm", None), "model", "") or "",
            result.get("models") or {},
        )
        if model_id:
            try:
                await client.set_model(session_id, model_id)
            except ACPError:
                pass  # best effort — Hermes stays on its default

        stop_event = getattr(interpreter, "stop_event", None)
        if stop_event is not None:
            watcher = asyncio.create_task(_watch_stop(stop_event, client, session_id))

        await client.prompt(session_id, _latest_user_text(interpreter))
    except ACPError as e:
        chunk_q.put(
            {
                "role": "assistant",
                "type": "message",
                "content": _format_error(e, client),
            }
        )
    finally:
        if watcher is not None:
            watcher.cancel()
        try:
            await client.close()
        except Exception:
            pass


def run(interpreter):
    """Synchronous generator yielding LMC chunks while Hermes runs the turn.

    Never raises out of the generator body for backend/transport errors — those
    are surfaced as assistant message chunks so the loop terminates cleanly.
    """
    chunk_q: queue.Queue = queue.Queue()
    loop = asyncio.new_event_loop()
    state: dict = {}

    def _thread_main() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_drive(interpreter, chunk_q, state))
        except Exception as e:  # pragma: no cover - defensive
            chunk_q.put(e)
        finally:
            chunk_q.put(_DONE)
            try:
                loop.close()
            except Exception:
                pass

    thread = threading.Thread(target=_thread_main, name="hermes-acp", daemon=True)
    thread.start()

    try:
        while True:
            item = chunk_q.get()
            if item is _DONE:
                break
            if isinstance(item, Exception):
                yield {
                    "role": "assistant",
                    "type": "message",
                    "content": _format_error(item, state.get("client")),
                }
                continue
            if isinstance(item, dict) and item.get("__permission__"):
                # Yield the confirmation chunk; this suspends the generator until
                # the UI sets interpreter._code_execution_approved and pulls again.
                yield item["lmc_chunk"]
                approved = _read_approval(interpreter)
                fut = item["future"]
                if not fut.done():
                    try:
                        fut.set_result(approved)
                    except Exception:
                        pass
                continue
            yield item
    except GeneratorExit:
        # Consumer aborted (Ctrl-C / stop_event). Cancel the in-flight prompt.
        client = state.get("client")
        session_id = state.get("session_id")
        if client is not None and session_id and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(client.cancel(session_id), loop)
            except Exception:
                pass
        raise
    finally:
        if not loop.is_closed():
            try:
                loop.call_soon_threadsafe(
                    lambda: [t.cancel() for t in asyncio.all_tasks(loop)]
                )
            except Exception:
                pass
        thread.join(timeout=5.0)
