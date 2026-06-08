"""Minimal async ACP (Agent Client Protocol) client over subprocess stdio.

Pure stdlib (``asyncio`` + ``json``) — deliberately no dependency on the
``agent-client-protocol`` package, so the fork's dependency surface stays
untouched (hermes-agent and its conflicting pins live entirely inside the
isolated ``uvx`` subprocess).

Wire format (validated against agent-client-protocol==0.9.0, protocol version 1):
newline-delimited JSON-RPC 2.0, **camelCase** field names. stdout carries the
protocol; stderr carries logs.

Client→server methods used here:
    initialize          {protocolVersion, clientCapabilities, clientInfo}
    session/new         {cwd, mcpServers} -> {sessionId, models, modes}
    session/set_model   {sessionId, modelId}
    session/prompt      {sessionId, prompt:[{type:"text",text}]} -> {stopReason, usage}
    session/cancel      {sessionId}   (notification)

Server→client traffic dispatched here:
    session/update              (notification) -> on_session_update(params)
    session/request_permission  (request)      -> on_permission_request(params) -> result
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
from collections.abc import Awaitable
from typing import Any, Callable

PROTOCOL_VERSION = 1


def _error_detail(error) -> str:
    """Pull the most human-readable string out of a JSON-RPC error object."""
    if isinstance(error, dict):
        data = error.get("data")
        if isinstance(data, dict) and data.get("details"):
            return str(data["details"])
        if error.get("message"):
            return str(error["message"])
    return str(error)


class ACPError(Exception):
    """Any ACP transport / protocol failure."""


class HermesNotInstalled(ACPError):
    """The Hermes subprocess could not be spawned (uvx or hermes missing)."""


class ACPClient:
    """Drives an ACP agent subprocess. One client == one subprocess == one loop."""

    def __init__(
        self,
        cmd: list[str],
        cwd: str,
        env: dict | None = None,
        stderr_ring: int = 200,
    ) -> None:
        self.cmd = cmd
        self.cwd = cwd
        self.env = env if env is not None else dict(os.environ)
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stderr_ring: collections.deque = collections.deque(maxlen=stderr_ring)
        self._closed = False

        # Handlers wired by the caller.
        self.on_session_update: Callable[[dict], None] | None = None
        self.on_permission_request: Callable[[dict], Awaitable[dict]] | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.cmd,
                cwd=self.cwd,
                env=self.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise HermesNotInstalled(str(e)) from e
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_drain())

    async def close(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if proc is not None:
            try:
                if proc.stdin is not None and not proc.stdin.is_closing():
                    proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()

    def stderr_tail(self, n: int = 20) -> str:
        return "\n".join(list(self._stderr_ring)[-n:])

    # ------------------------------------------------------------------ transport

    async def _stderr_drain(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                self._stderr_ring.append(line.decode("utf-8", "replace").rstrip())
        except asyncio.CancelledError:
            return
        except Exception:
            return

    async def _reader_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break  # EOF — subprocess closed stdout
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue  # not a protocol frame (stray output); ignore
                await self._dispatch(msg)
        except asyncio.CancelledError:
            return
        finally:
            self._fail_pending(
                ACPError(
                    "ACP connection closed"
                    + (f": {self.stderr_tail(10)}" if self._stderr_ring else "")
                )
            )

    def _fail_pending(self, exc: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    async def _write(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise ACPError("ACP subprocess stdin is not available")
        data = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        proc.stdin.write(data)
        await proc.stdin.drain()

    async def _dispatch(self, msg: dict) -> None:
        # Response to one of our requests.
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut is not None and not fut.done():
                if "error" in msg:
                    fut.set_exception(ACPError(_error_detail(msg["error"])))
                else:
                    fut.set_result(msg.get("result"))
            return

        method = msg.get("method")
        if method is None:
            return

        if "id" in msg:
            # Server-initiated request — must be answered.
            await self._handle_server_request(msg)
        else:
            # Notification.
            if method == "session/update" and self.on_session_update is not None:
                try:
                    self.on_session_update(msg.get("params") or {})
                except Exception:
                    pass

    async def _handle_server_request(self, msg: dict) -> None:
        method = msg.get("method")
        params = msg.get("params") or {}
        rid = msg["id"]
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": rid}
        try:
            if (
                method == "session/request_permission"
                and self.on_permission_request is not None
            ):
                result = await self.on_permission_request(params)
                reply["result"] = result if result is not None else {}
            else:
                reply["error"] = {
                    "code": -32601,
                    "message": f"Method not found: {method}",
                }
        except Exception as e:  # never let a handler crash the reader
            reply["error"] = {"code": -32603, "message": str(e)}
        try:
            await self._write(reply)
        except Exception:
            pass

    # ------------------------------------------------------------------ requests

    async def _send_request(self, method: str, params: dict | None = None) -> Any:
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._write(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        )
        return await fut

    async def _send_notification(self, method: str, params: dict | None = None) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ------------------------------------------------------------------ high-level

    async def initialize(
        self, client_name: str = "open-interpreter", client_version: str = "0.1"
    ) -> dict:
        return await self._send_request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {},
                "clientInfo": {"name": client_name, "version": client_version},
            },
        )

    async def new_session(self, cwd: str, mcp_servers: list | None = None) -> dict:
        return await self._send_request(
            "session/new", {"cwd": cwd, "mcpServers": mcp_servers or []}
        )

    async def set_model(self, session_id: str, model_id: str) -> dict:
        return await self._send_request(
            "session/set_model", {"sessionId": session_id, "modelId": model_id}
        )

    async def prompt(self, session_id: str, text: str) -> dict:
        return await self._send_request(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
        )

    async def cancel(self, session_id: str) -> None:
        await self._send_notification("session/cancel", {"sessionId": session_id})
