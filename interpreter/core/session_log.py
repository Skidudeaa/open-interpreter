"""Always-on structured session logging.

Writes one JSON object per line (JSONL) to
``~/.open-interpreter/session_logs/<session_id>.jsonl``.

This is the durable, *untruncated* corpus for analysis and fine-tuning. It is
deliberately independent of the cc-sidecar observability daemon (which stays as
the live, queryable view and truncates payloads to 500 chars). Where the sidecar
answers "what is happening right now", this log answers "exactly what happened,
in full, so I can tune the system to my taste".

Every record carries a provenance envelope so records stay comparable as the
system's prompts, models, and code change over time::

    {
      "ts": "2026-07-19T14:30:00.123456",
      "epoch_ms": 1784...,
      "session_id": "20260719_143000",
      "seq": 42,
      "oi_version": "0.4.3",
      "git_sha": "9c0b3c20",
      "kind": "llm_request",
      ... record-specific fields ...
    }

Record ``kind``s emitted today:
  - ``session_start``   — one per logger, provenance + settings snapshot
  - ``llm_request``     — one per ``Llm.run()`` call: model, params, latency, caller role
  - ``agent_call``      — start/end of every orchestrated agent (role, model, timing)
  - ``workflow_decision`` — the routing verdict + how it was reached
  - ``terminal_output`` — coalesced message/code/console text shown on screen
  - ``agent_event``     — raw AGENT_* lifecycle events (timing, task, parent)
  - ``ui_event`` / ``system`` / ``event`` — mode changes, cancels, confirmations, etc.
  - ``user_signal``     — explicit dissatisfaction/steering signals (%retry, %reflect)
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import subprocess
import threading
import time
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any

# The role of the code path that made the current LLM call. Set by the agent
# orchestrator around each agent run (and around the workflow classifier) so
# every ``llm_request`` record can be attributed. Defaults to the main loop.
#
# ContextVars are per-thread and inherited by threads at creation time, so a
# value set in the thread that invokes ``agent.run()`` is visible to the
# ``Llm.run()`` call that agent makes synchronously.
current_caller_role: ContextVar[str] = ContextVar("oi_caller_role", default="main")


def sha8(text: str) -> str:
    """Short stable hash — used for system-prompt fingerprints so prompt edits
    can be correlated with behavior changes without storing the full prompt."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def _default_log_dir() -> Path:
    return Path.home() / ".open-interpreter" / "session_logs"


def _oi_version() -> str:
    try:
        from importlib.metadata import version

        return version("open-interpreter")
    except Exception:
        try:
            import interpreter

            return getattr(interpreter, "__version__", "unknown")
        except Exception:
            return "unknown"


def _git_sha() -> str:
    try:
        pkg_dir = Path(__file__).resolve().parent
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(pkg_dir),
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        if out.returncode == 0:
            return out.stdout.strip() or "unknown"
    except Exception:
        pass
    return "unknown"


class _LlmRequestRecord:
    """Live handle for one in-flight ``Llm.run()`` call.

    Records start/first-token/end timing plus any usage the streamed chunks
    happen to expose, then writes a single ``llm_request`` line on ``end()``.
    Token counts are best-effort: Open Interpreter's stream wrappers yield LMC
    dicts, not raw provider chunks, so usage is often absent — latency, model,
    params and caller role are always captured.
    """

    def __init__(self, logger: SessionLogger, fields: dict[str, Any]):
        self._logger = logger
        self._fields = fields
        self._t0 = time.perf_counter()
        self._ttft_ms: float | None = None
        self._usage: dict[str, Any] | None = None
        self._finish_reason: str | None = None
        self._done = False

    def first_token(self) -> None:
        if self._ttft_ms is None:
            self._ttft_ms = (time.perf_counter() - self._t0) * 1000.0

    def observe_chunk(self, chunk: Any) -> None:
        if not isinstance(chunk, dict):
            return
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self._usage = usage
        fr = chunk.get("finish_reason") or chunk.get("stop_reason")
        if fr:
            self._finish_reason = fr

    def end(self, status: str = "ok", error: str | None = None) -> None:
        if self._done:
            return
        self._done = True
        total_ms = (time.perf_counter() - self._t0) * 1000.0
        rec = dict(self._fields)
        rec.update(
            {
                "status": status,
                "ttft_ms": (
                    round(self._ttft_ms, 1) if self._ttft_ms is not None else None
                ),
                "total_ms": round(total_ms, 1),
                "finish_reason": self._finish_reason,
                "usage": self._usage,
            }
        )
        if error:
            rec["error"] = error[:1000]
        self._logger.log("llm_request", **rec)


class SessionLogger:
    """Thread-safe append-only JSONL logger. One instance per interpreter."""

    def __init__(
        self,
        session_id: str | None = None,
        log_dir: str | os.PathLike | None = None,
        enabled: bool = True,
    ):
        env = os.environ.get("OI_SESSION_LOG", "").strip().lower()
        if env in {"0", "false", "no", "off"}:
            enabled = False
        self.enabled = enabled

        self.session_id = (
            session_id
            or os.environ.get("CLAUDE_SESSION_ID")
            or datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        self._lock = threading.Lock()
        self._seq = 0
        self._fh = None
        self._closed = False
        self.path: Path | None = None
        self.oi_version = _oi_version()
        self.git_sha = _git_sha()

        if not self.enabled:
            return

        try:
            d = Path(log_dir) if log_dir else _default_log_dir()
            d.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass
            self.path = d / f"{self.session_id}.jsonl"
            self._fh = open(self.path, "a", encoding="utf-8")
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            atexit.register(self.close)
        except Exception:
            # Never let logging break the interpreter.
            self.enabled = False
            self._fh = None

    # -- core write -------------------------------------------------------
    def log(self, kind: str, **fields: Any) -> None:
        if not self.enabled or self._fh is None or self._closed:
            return
        try:
            now = time.time()
            with self._lock:
                self._seq += 1
                seq = self._seq
                record = {
                    "ts": datetime.now().isoformat(),
                    "epoch_ms": int(now * 1000),
                    "session_id": self.session_id,
                    "seq": seq,
                    "oi_version": self.oi_version,
                    "git_sha": self.git_sha,
                    "kind": kind,
                }
                record.update(fields)
                self._fh.write(json.dumps(record, default=str) + "\n")
                self._fh.flush()
        except Exception:
            # Swallow — a logging failure must never propagate.
            pass

    # -- LLM request instrumentation -------------------------------------
    def llm_request_begin(
        self,
        model: str,
        params: dict[str, Any] | None = None,
        system_message: str | None = None,
        n_messages: int | None = None,
    ) -> _LlmRequestRecord:
        fields: dict[str, Any] = {
            "model": model,
            "caller_role": current_caller_role.get(),
            "params": params or {},
            "n_messages": n_messages,
        }
        if system_message is not None:
            fields["system_prompt_sha"] = sha8(system_message)
            fields["system_prompt_len"] = len(system_message)
        return _LlmRequestRecord(self, fields)

    # -- lifecycle --------------------------------------------------------
    def session_start(self, **snapshot: Any) -> None:
        self.log("session_start", **snapshot)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._fh is not None:
                try:
                    self._fh.flush()
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None

    # -- EventBus sink ----------------------------------------------------
    _CONSOLE_TYPES = {"CONSOLE_OUTPUT", "CONSOLE_ERROR", "CONSOLE_ACTIVE_LINE"}

    def attach_event_bus(self) -> bool:
        """Subscribe a coalescing sink to the global UI EventBus.

        Captures the semantic content shown on the terminal (message/code/console
        text, coalesced per block) plus all lifecycle/mode/cancel events. Returns
        True if attached. Idempotent (subscribe_all dedupes).
        """
        if not self.enabled:
            return False
        try:
            from ..terminal_interface.components.ui_events import get_event_bus

            self._msg_buf: list[str] = []
            self._code_buf: list[str] = []
            self._console_buf: list[str] = []
            get_event_bus().subscribe_all(self._on_ui_event)
            return True
        except Exception:
            return False

    def _flush_console(self) -> None:
        if getattr(self, "_console_buf", None):
            text = "".join(self._console_buf)
            self._console_buf = []
            if text:
                self.log("terminal_output", channel="console", content=text)

    def _on_ui_event(self, event: Any) -> None:
        # Must never raise into the bus.
        try:
            etype = getattr(event.type, "name", str(event.type))
            data = event.data if isinstance(getattr(event, "data", None), dict) else {}

            if etype not in self._CONSOLE_TYPES:
                self._flush_console()

            if etype == "MESSAGE_START":
                self._msg_buf = []
            elif etype == "MESSAGE_CHUNK":
                self._msg_buf.append(str(data.get("content", "")))
            elif etype == "MESSAGE_END":
                text = "".join(self._msg_buf)
                self._msg_buf = []
                if text:
                    self.log("terminal_output", channel="message", content=text)
            elif etype == "CODE_START":
                self._code_buf = []
            elif etype == "CODE_CHUNK":
                self._code_buf.append(str(data.get("content", "")))
            elif etype == "CODE_END":
                text = "".join(self._code_buf)
                self._code_buf = []
                self.log(
                    "terminal_output",
                    channel="code",
                    language=data.get("format") or data.get("language"),
                    content=text,
                )
            elif etype in self._CONSOLE_TYPES:
                content = str(data.get("content", data.get("message", "")))
                if content:
                    self._console_buf.append(content)
            elif etype.startswith("AGENT_"):
                self.log("agent_event", event=etype, **data)
            elif etype.startswith("UI_") or etype.startswith("CONFIRMATION_"):
                self.log("ui_event", event=etype, **data)
            elif etype.startswith("SYSTEM_"):
                self.log("system", event=etype, **data)
            elif etype in {"MEMORY_RECORD", "FILE_CHANGE", "GIT_COMMIT"}:
                self.log("memory_event", event=etype, **data)
            # Everything else (ACTIVITY, RERANK_*, VALIDATION_*, ...) is skippable
            # noise for the corpus; the meaningful lifecycle is already covered.
        except Exception:
            pass


def summarize(path: str | os.PathLike) -> str:
    """Human-readable summary of one session-log JSONL file.

    Run as: ``python -m interpreter.core.session_log <file.jsonl>``
    (no arg → newest file in ~/.open-interpreter/session_logs/).
    """
    from collections import Counter, defaultdict

    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass
    if not records:
        return f"{path}: empty"

    kinds = Counter(r["kind"] for r in records)
    llm = [r for r in records if r["kind"] == "llm_request"]
    by_model = Counter(r.get("model") for r in llm)
    by_role = Counter(r.get("caller_role") for r in llm)
    lat = [r["total_ms"] for r in llm if isinstance(r.get("total_ms"), (int, float))]
    decisions = Counter(
        f"{r.get('workflow')}/{r.get('method')}"
        for r in records
        if r["kind"] == "workflow_decision"
    )
    agent_ends = [
        r for r in records if r["kind"] == "agent_call" and r.get("phase") == "end"
    ]
    agent_ms = defaultdict(list)
    for r in agent_ends:
        if isinstance(r.get("elapsed_ms"), (int, float)):
            agent_ms[r.get("role")].append(r["elapsed_ms"])
    signals = Counter(r.get("signal") for r in records if r["kind"] == "user_signal")

    first, last = records[0], records[-1]
    out = []
    out.append(f"Session {first['session_id']}  ({str(path)})")
    out.append(
        f"  {first['ts']} → {last['ts']}   git {first.get('git_sha')}  "
        f"oi {first.get('oi_version')}   {len(records)} records"
    )
    out.append(
        "  record kinds: " + ", ".join(f"{k}={n}" for k, n in kinds.most_common())
    )
    if llm:
        out.append(f"\n  LLM calls: {len(llm)}")
        out.append(
            "    by model: " + ", ".join(f"{m}={n}" for m, n in by_model.most_common())
        )
        out.append(
            "    by role:  " + ", ".join(f"{r}={n}" for r, n in by_role.most_common())
        )
        if lat:
            lat_sorted = sorted(lat)
            p50 = lat_sorted[len(lat_sorted) // 2]
            out.append(
                f"    latency ms: min={min(lat):.0f} p50={p50:.0f} max={max(lat):.0f}"
            )
    if decisions:
        out.append("\n  workflow decisions (type/method):")
        for d, n in decisions.most_common():
            out.append(f"    {d}: {n}")
    if agent_ms:
        out.append("\n  agent runs (role: count, avg ms):")
        for role, times in agent_ms.items():
            out.append(f"    {role}: {len(times)}, avg {sum(times)/len(times):.0f}ms")
    if signals:
        out.append(
            "\n  user signals: " + ", ".join(f"{s}={n}" for s, n in signals.items())
        )
    return "\n".join(out)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        d = _default_log_dir()
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not files:
            print(f"No session logs in {d}")
            raise SystemExit(0)
        target = files[-1]
    print(summarize(target))
