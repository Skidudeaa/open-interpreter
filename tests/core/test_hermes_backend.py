"""Tests for the Hermes ACP backend (interpreter/core/backends/).

Everything runs against an in-process **fake ACP server** wired to a fake
subprocess — no `uvx`, no network, no real hermes-agent. The fake speaks the
real newline-delimited JSON-RPC 2.0 / camelCase wire format, so these exercise
the actual transport, the async↔sync thread bridge, and the permission handshake.
"""

import asyncio
import json
import threading
import time
import types

from interpreter.core.backends import acp_client, hermes_backend

# --------------------------------------------------------------------------- fakes


class _FakeStdin:
    """Captures client→server frames and drives the fake server synchronously."""

    def __init__(self, server):
        self._server = server
        self._closing = False

    def write(self, data: bytes) -> None:
        for line in data.decode("utf-8").splitlines():
            line = line.strip()
            if line:
                self._server.handle_client_frame(json.loads(line))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self._closing = True
        self._server.stdin_closed()

    def is_closing(self) -> bool:
        return self._closing


class _FakeProcess:
    def __init__(self, server):
        self._server = server
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.stdin = _FakeStdin(server)
        self._exited = asyncio.Event()
        server.bind(self)

    async def wait(self) -> int:
        await self._exited.wait()
        return 0

    def kill(self) -> None:
        self._exited.set()

    def mark_exited(self) -> None:
        self._exited.set()


class FakeHermesServer:
    """Minimal scriptable ACP agent. Feeds frames to the client's stdout reader."""

    def __init__(
        self,
        updates=None,
        permission=None,
        prompt_result=None,
        new_session_result=None,
        hang_on_prompt=False,
    ):
        self.updates = updates or []
        self.permission = permission  # {"toolCall": {...}, "options": [...]}
        self.prompt_result = prompt_result or {"stopReason": "end_turn"}
        self.new_session_result = new_session_result or {
            "sessionId": "sess-1",
            "models": {"availableModels": [], "currentModelId": None},
            "modes": {},
        }
        self.hang_on_prompt = hang_on_prompt

        self._proc = None
        self._server_req_id = 1000
        self._pending_prompt_id = None
        # observable state
        self.received_methods = []
        self.permission_reply = None
        self.cancelled = False
        self.set_model_called_with = None

    def bind(self, proc):
        self._proc = proc

    def _send(self, frame: dict) -> None:
        data = (json.dumps(frame, separators=(",", ":")) + "\n").encode("utf-8")
        self._proc.stdout.feed_data(data)

    def _emit_updates_and_finish(self):
        for upd in self.updates:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {"sessionId": "sess-1", "update": upd},
                }
            )
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self._pending_prompt_id,
                "result": self.prompt_result,
            }
        )
        self._pending_prompt_id = None

    def handle_client_frame(self, msg: dict) -> None:
        method = msg.get("method")

        # Reply to a server-initiated request (e.g. the permission outcome).
        if method is None and "id" in msg and ("result" in msg or "error" in msg):
            self.permission_reply = msg.get("result")
            # Permission resolved → now stream the turn.
            self._emit_updates_and_finish()
            return

        self.received_methods.append(method)

        if method == "initialize":
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {
                        "protocolVersion": 1,
                        "agentInfo": {"name": "hermes-agent", "version": "0.16.0"},
                        "agentCapabilities": {},
                        "authMethods": [],
                    },
                }
            )
        elif method == "session/new":
            self._send(
                {"jsonrpc": "2.0", "id": msg["id"], "result": self.new_session_result}
            )
        elif method == "session/set_model":
            self.set_model_called_with = (msg.get("params") or {}).get("modelId")
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        elif method == "session/prompt":
            self._pending_prompt_id = msg["id"]
            if self.permission is not None:
                self._server_req_id += 1
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": self._server_req_id,
                        "method": "session/request_permission",
                        "params": {"sessionId": "sess-1", **self.permission},
                    }
                )
                # prompt result is sent after the client replies (handle reply above)
            elif self.hang_on_prompt:
                pass  # never finish on our own — wait for session/cancel
            else:
                self._emit_updates_and_finish()
        elif method == "session/cancel":
            self.cancelled = True
            if self._pending_prompt_id is not None:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": self._pending_prompt_id,
                        "result": {"stopReason": "cancelled"},
                    }
                )
                self._pending_prompt_id = None

    def stdin_closed(self) -> None:
        if self._proc is not None:
            self._proc.mark_exited()


def _install_fake(monkeypatch, server):
    async def fake_create(*args, **kwargs):
        return _FakeProcess(server)

    monkeypatch.setattr(acp_client.asyncio, "create_subprocess_exec", fake_create)


def _make_interpreter(message="do a thing", model="gemini/gemini-3.5-flash"):
    interp = types.SimpleNamespace()
    interp.messages = [{"role": "user", "type": "message", "content": message}]
    interp.computer = types.SimpleNamespace(cwd="/tmp")
    interp.llm = types.SimpleNamespace(model=model)
    interp.stop_event = threading.Event()
    return interp


def _drive(interp, approve=None, timeout=10.0):
    """Iterate run() to completion on a watchdog, optionally answering approvals."""
    chunks = []
    deadline = time.time() + timeout
    gen = hermes_backend.run(interp)
    for chunk in gen:
        chunks.append(chunk)
        if chunk.get("type") == "confirmation" and approve is not None:
            interp._code_execution_approved = approve
        if time.time() > deadline:  # pragma: no cover - safety
            gen.close()
            raise AssertionError("run() did not terminate in time")
    return chunks


# --------------------------------------------------------------------------- tests


def test_session_update_to_chunk_translation(monkeypatch):
    server = FakeHermesServer(
        updates=[
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "Hello"},
            },
            {
                "sessionUpdate": "tool_call",
                "kind": "execute",
                "content": [
                    {"type": "content", "content": {"type": "text", "text": "ls -la"}}
                ],
            },
            {
                "sessionUpdate": "tool_call_update",
                "status": "completed",
                "content": [
                    {"type": "content", "content": {"type": "text", "text": "out.txt"}}
                ],
            },
        ]
    )
    _install_fake(monkeypatch, server)
    chunks = _drive(_make_interpreter())

    assert {"role": "assistant", "type": "message", "content": "Hello"} in chunks
    assert {
        "role": "assistant",
        "type": "code",
        "format": "shell",
        "content": "ls -la",
    } in chunks
    assert {
        "role": "computer",
        "type": "console",
        "format": "output",
        "content": "out.txt",
    } in chunks
    assert "initialize" in server.received_methods
    assert "session/new" in server.received_methods
    assert "session/prompt" in server.received_methods


def test_permission_handshake_approve(monkeypatch):
    server = FakeHermesServer(
        permission={
            "toolCall": {
                "toolCallId": "tc-1",
                "kind": "execute",
                "title": "Run command",
                "content": [
                    {"type": "content", "content": {"type": "text", "text": "rm x"}}
                ],
            },
            "options": [
                {"optionId": "allow_once", "kind": "allow_once", "name": "Allow"},
                {"optionId": "deny", "kind": "reject_once", "name": "Deny"},
            ],
        },
        updates=[
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "done"},
            }
        ],
    )
    _install_fake(monkeypatch, server)
    interp = _make_interpreter()
    chunks = _drive(interp, approve=True)

    # The confirmation chunk was surfaced through the normal LMC contract.
    confs = [c for c in chunks if c.get("type") == "confirmation"]
    assert len(confs) == 1
    assert confs[0]["content"]["content"] == "rm x"
    # Approval was relayed to the server with the allow option.
    assert server.permission_reply == {
        "outcome": {"outcome": "selected", "optionId": "allow_once"}
    }
    # Sentinel cleaned up after read.
    assert not hasattr(interp, "_code_execution_approved")
    assert {"role": "assistant", "type": "message", "content": "done"} in chunks


def test_permission_handshake_deny(monkeypatch):
    server = FakeHermesServer(
        permission={
            "toolCall": {
                "toolCallId": "tc-2",
                "kind": "execute",
                "title": "Run",
                "content": [],
            },
            "options": [
                {"optionId": "allow_once", "kind": "allow_once", "name": "Allow"},
                {"optionId": "deny", "kind": "reject_once", "name": "Deny"},
            ],
        }
    )
    _install_fake(monkeypatch, server)
    chunks = _drive(_make_interpreter(), approve=False)

    assert any(c.get("type") == "confirmation" for c in chunks)
    assert server.permission_reply == {
        "outcome": {"outcome": "selected", "optionId": "deny"}
    }


def test_permission_timeout_auto_denies(monkeypatch):
    """The handler itself auto-denies if the sync side never resolves the future."""
    monkeypatch.setattr(hermes_backend, "PERMISSION_TIMEOUT", 0.2)
    import queue

    chunk_q = queue.Queue()
    handler = hermes_backend._make_permission_handler(chunk_q)
    params = {
        "toolCall": {
            "toolCallId": "tc-3",
            "kind": "execute",
            "title": "x",
            "content": [],
        },
        "options": [{"optionId": "deny", "kind": "reject_once", "name": "Deny"}],
    }
    outcome = asyncio.run(handler(params))

    # A control item was enqueued for the sync side (which we deliberately ignore).
    item = chunk_q.get_nowait()
    assert item["__permission__"] is True
    assert outcome == {"outcome": {"outcome": "selected", "optionId": "deny"}}


def test_graceful_degradation_when_uvx_missing(monkeypatch):
    async def boom(*args, **kwargs):
        raise FileNotFoundError("uvx not found")

    monkeypatch.setattr(acp_client.asyncio, "create_subprocess_exec", boom)
    chunks = _drive(_make_interpreter())

    assert len(chunks) == 1
    assert chunks[0]["role"] == "assistant"
    assert "uvx" in chunks[0]["content"]
    assert "--backend oi" in chunks[0]["content"]


def test_cancellation(monkeypatch):
    server = FakeHermesServer(hang_on_prompt=True)
    _install_fake(monkeypatch, server)
    interp = _make_interpreter()

    # Trip the stop_event shortly after the turn starts; the watcher cancels.
    threading.Timer(0.4, interp.stop_event.set).start()
    chunks = _drive(interp, timeout=10.0)

    assert server.cancelled is True
    # No assistant/console content is required; the turn ends cleanly.
    assert isinstance(chunks, list)


def test_model_mapping_best_effort(monkeypatch):
    server = FakeHermesServer(
        new_session_result={
            "sessionId": "sess-1",
            "models": {
                "availableModels": [
                    {"modelId": "openrouter:gemini-3.5-flash", "name": "g"},
                    {"modelId": "openai:gpt-4o", "name": "g4"},
                ],
                "currentModelId": "openai:gpt-4o",
            },
            "modes": {},
        }
    )
    _install_fake(monkeypatch, server)
    _drive(_make_interpreter(model="gemini/gemini-3.5-flash"))
    assert server.set_model_called_with == "openrouter:gemini-3.5-flash"


def test_model_alias_resolution():
    """Short aliases, full ids, and litellm-style ids all map to Hermes modelIds."""
    catalog = {
        "availableModels": [
            {"modelId": m}
            for m in [
                "openrouter:anthropic/claude-opus-4.8",
                "openrouter:anthropic/claude-opus-4.8-fast",
                "openrouter:anthropic/claude-sonnet-4.6",
                "openrouter:openai/gpt-5.5",
                "openrouter:openai/gpt-5.5-pro",
                "openrouter:google/gemini-3.1-pro-preview",
                "openrouter:google/gemini-3.5-flash",
            ]
        ]
    }
    m = hermes_backend._map_model
    # alias → base variant (not -fast / -pro)
    assert m("opus", catalog) == "openrouter:anthropic/claude-opus-4.8"
    assert m("opus-fast", catalog) == "openrouter:anthropic/claude-opus-4.8-fast"
    assert m("sonnet", catalog) == "openrouter:anthropic/claude-sonnet-4.6"
    assert m("gpt", catalog) == "openrouter:openai/gpt-5.5"
    assert m("gemini", catalog) == "openrouter:google/gemini-3.1-pro-preview"
    # full id → exact
    assert m("openrouter:openai/gpt-5.5", catalog) == "openrouter:openai/gpt-5.5"
    # litellm-style id → mapped
    assert m("gemini/gemini-3.5-flash", catalog) == "openrouter:google/gemini-3.5-flash"
    # bare substring picks the shortest (base) variant
    assert m("gpt-5.5", catalog) == "openrouter:openai/gpt-5.5"
    # unknown → None (leave Hermes on its default)
    assert m("bogus-xyz", catalog) is None
    assert m("", catalog) is None


def test_core_branch_selects_hermes(monkeypatch):
    """interpreter.backend == 'hermes' routes _respond_and_store through hermes_backend."""
    from interpreter import OpenInterpreter

    def fake_run(interpreter):
        yield {"role": "assistant", "type": "message", "content": "from-hermes"}

    monkeypatch.setattr(hermes_backend, "run", fake_run)

    interp = OpenInterpreter()
    interp.backend = "hermes"
    interp.messages = [{"role": "user", "type": "message", "content": "hi"}]

    chunks = list(interp._respond_and_store())
    contents = [c.get("content") for c in chunks if c.get("type") == "message"]
    assert "from-hermes" in contents


def test_compose_prompt_prepends_system_message():
    """Phase 1: hermes turns carry OI's assembled system message (ACP has no
    separate system-prompt slot) ahead of the user request."""
    from interpreter.core.core import OpenInterpreter

    interp = OpenInterpreter()
    interp.system_message = "SENTINEL_SYSTEM_PROMPT"
    interp.messages = [{"role": "user", "type": "message", "content": "do the thing"}]

    out = hermes_backend._compose_prompt(interp)

    assert "SENTINEL_SYSTEM_PROMPT" in out
    assert "do the thing" in out
    assert out.index("SENTINEL_SYSTEM_PROMPT") < out.index("do the thing")


def test_compose_prompt_falls_back_to_user_text_on_build_failure(monkeypatch):
    """Non-blocking: if the system message can't be built, hermes still gets the
    user request (prior behavior)."""
    from interpreter.core.core import OpenInterpreter
    from interpreter.core.services import system_message_builder as smb

    interp = OpenInterpreter()
    interp.messages = [
        {"role": "user", "type": "message", "content": "just the user text"}
    ]

    def boom(self, interpreter):
        raise RuntimeError("build failed")

    monkeypatch.setattr(smb.SystemMessageBuilder, "build", boom)

    out = hermes_backend._compose_prompt(interp)
    assert out == "just the user text"
