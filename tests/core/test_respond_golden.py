"""Golden chunk-sequence regression tests for ``respond()``.

These pin the *observable output contract* of the ``respond()`` generator so the
respond-decomposition refactor (extracting services + in-file helpers) can be
proven behavior-identical. They assert the exact ordered sequence of chunk
shapes each representative flow produces.

Two driving modes are used deliberately:

* Simple flows (message-only, loop-continuation, MCP-sentinel) drive ``respond()``
  **directly** to pin its raw yield contract.
* Code-execution flows drive through ``_respond_and_store`` because that consumer
  is what appends the computer message between yields — flipping ``messages[-1]``
  off ``"code"`` so the loop terminates. Driving a code flow through ``respond()``
  directly would re-enter the code branch forever. Going through the consumer also
  covers the start/end flag-delimiter assembly at the respond<->consumer boundary,
  the weakest-tested seam.

The Flow-5 validation test intentionally pins the *current* buggy no-op gate
(``VALIDATION_START`` fires but ``VALIDATION_END`` never does). Step 6 of the
refactor fixes that bug and deliberately updates this golden.
"""

import pytest

from interpreter.core.core import OpenInterpreter
from interpreter.core.respond import respond
from interpreter.terminal_interface.components.ui_events import (
    EventType,
    get_event_bus,
    reset_event_bus,
)


def _quiet_interpreter() -> OpenInterpreter:
    """A minimal interpreter with every optional feature off (mirrors
    tests/core/agents/test_execution_spine.py)."""
    interp = OpenInterpreter()
    interp.enable_agents = False
    interp.enable_semantic_memory = False
    interp.enable_validation = False
    interp.enable_tracing = False
    interp.enable_auto_test = False
    interp.show_file_diffs = False
    interp.auto_commit = False
    interp.loop = False
    return interp


def _triple(chunk: dict) -> tuple:
    """Project a chunk to its (role, type, format) identity."""
    return (chunk.get("role"), chunk.get("type"), chunk.get("format"))


def _flagged(chunk: dict) -> tuple:
    """Project a delimited chunk (from _respond_and_store) to
    (role, type, format, flag) where flag is 'start'/'end'/None."""
    flag = "start" if chunk.get("start") else "end" if chunk.get("end") else None
    return (chunk.get("role"), chunk.get("type"), chunk.get("format"), flag)


@pytest.fixture(autouse=True)
def _fresh_event_bus():
    """Isolate event capture per test."""
    reset_event_bus()
    yield
    reset_event_bus()


def _capture_events() -> list:
    """Subscribe to all events and return the mutable list they land in."""
    captured = []
    get_event_bus().subscribe_all(lambda ev: captured.append(ev))
    return captured


# --------------------------------------------------------------------------
# Flow 1 — message-only completion
# --------------------------------------------------------------------------
def test_golden_message_only():
    interp = _quiet_interpreter()
    interp.messages = [{"role": "user", "type": "message", "content": "hi"}]

    def fake_llm_run(messages):
        yield {"type": "message", "content": "hello there"}

    interp.llm.run = fake_llm_run

    chunks = list(respond(interp))

    assert [_triple(c) for c in chunks] == [("assistant", "message", None)]
    assert chunks[0]["content"] == "hello there"


# --------------------------------------------------------------------------
# Flow 2 — code execution with approval (through the real consumer seam)
# --------------------------------------------------------------------------
def test_golden_code_execution_with_approval():
    interp = _quiet_interpreter()
    interp.auto_run = True
    interp.messages = [
        {
            "role": "user",
            "type": "message",
            "content": "run it",
        },
        {
            "role": "assistant",
            "type": "code",
            "format": "python",
            "content": "print('hi')",
        },
    ]

    run_calls = []

    def fake_run(language, code, stream=False):
        run_calls.append((language, code, stream))
        yield {"type": "console", "format": "output", "content": "hi\n"}

    llm_calls = {"n": 0}

    def fake_llm_run(messages):
        # Second turn (after the computer message is appended) terminates.
        llm_calls["n"] += 1
        yield {"type": "message", "content": "done"}

    interp.computer.run = fake_run
    interp.llm.run = fake_llm_run

    chunks = list(interp._respond_and_store())

    # Code actually ran, exactly once.
    assert len(run_calls) == 1
    assert run_calls[0][0] == "python"

    # The delimited boundary sequence the UI/consumer depend on.
    assert [_flagged(c) for c in chunks] == [
        ("computer", "console", None, "start"),
        ("computer", "console", "output", None),
        ("computer", "console", "active_line", None),
        ("computer", "console", None, "end"),
        ("assistant", "message", None, "start"),
        ("assistant", "message", None, None),
        ("assistant", "message", None, "end"),
    ]
    # The active_line terminator carries content None.
    active_line = next(c for c in chunks if c.get("format") == "active_line")
    assert active_line["content"] is None


# --------------------------------------------------------------------------
# Flow 3 — loop-message continuation emits exactly one separator
# --------------------------------------------------------------------------
def test_golden_loop_continuation_separator():
    interp = _quiet_interpreter()
    interp.loop = True
    interp.messages = [
        {"role": "user", "type": "message", "content": "keep going"},
        {"role": "assistant", "type": "message", "content": "step one"},
    ]

    calls = {"n": 0}

    def fake_llm_run(messages):
        calls["n"] += 1
        if calls["n"] >= 2:
            # Terminate the loop on the second turn.
            interp.loop = False
        yield {"type": "message", "content": f"turn {calls['n']}"}

    interp.llm.run = fake_llm_run

    chunks = list(respond(interp))

    separators = [c for c in chunks if c.get("content") == "\n\n"]
    assert len(separators) == 1
    assert _triple(separators[0]) == ("assistant", "message", None)
    # Separator sits between the two LLM turns.
    sep_index = chunks.index(separators[0])
    assert chunks[sep_index - 1]["content"] == "turn 1"
    assert chunks[sep_index + 1]["content"] == "turn 2"


# --------------------------------------------------------------------------
# Flow 4 — the internal _mcp_continue sentinel is never forwarded
# --------------------------------------------------------------------------
def test_golden_mcp_sentinel_is_filtered(monkeypatch):
    interp = _quiet_interpreter()
    interp.messages = [
        {"role": "user", "type": "message", "content": "call the tool"},
        {"role": "assistant", "type": "mcp_tool", "content": "{}"},
    ]

    def fake_mcp_tool(interpreter):
        # Flip the last message off "mcp_tool" so the next loop turn winds down
        # instead of re-entering this branch forever.
        interpreter.messages[-1] = {
            "role": "assistant",
            "type": "message",
            "content": "tool finished",
        }
        yield {
            "role": "computer",
            "type": "console",
            "format": "output",
            "content": "tool ran\n",
        }
        yield {"_mcp_continue": True}

    # LLM yields nothing, so messages[-1] stays "mcp_tool" and the tool branch runs.
    def fake_llm_run(messages):
        return
        yield  # pragma: no cover - makes this an empty generator

    monkeypatch.setattr("interpreter.core.respond._run_mcp_tool", fake_mcp_tool)
    interp.llm.run = fake_llm_run

    chunks = list(respond(interp))

    # The sentinel dict must never surface to callers.
    assert all("_mcp_continue" not in c for c in chunks)
    # The tool's real console output did surface (and the loop continued + ended).
    assert any(c.get("content") == "tool ran\n" for c in chunks)


# --------------------------------------------------------------------------
# Flow 5 — validation gate: pins the CURRENT buggy no-op behavior
# --------------------------------------------------------------------------
def test_golden_validation_gate_runs_on_valid_code():
    """After the Step-6 fix, the gate actually validates: both VALIDATION_START
    and VALIDATION_END fire. For valid code (print('hi')) there are no
    [Validation] error chunks, and the status indicator reports 'validated'.
    """
    interp = _quiet_interpreter()
    interp.enable_validation = True
    interp.auto_run = True
    interp.messages = [
        {"role": "user", "type": "message", "content": "run it"},
        {
            "role": "assistant",
            "type": "code",
            "format": "python",
            "content": "print('hi')",
        },
    ]

    def fake_run(language, code, stream=False):
        yield {"type": "console", "format": "output", "content": "hi\n"}

    def fake_llm_run(messages):
        yield {"type": "message", "content": "done"}

    interp.computer.run = fake_run
    interp.llm.run = fake_llm_run

    events = _capture_events()
    chunks = list(interp._respond_and_store())

    event_types = {ev.type for ev in events}
    assert EventType.VALIDATION_START in event_types
    assert EventType.VALIDATION_END in event_types  # fixed: end now fires

    # Valid code -> no [Validation] error chunks.
    assert not any(
        "[Validation]" in (c.get("content") or "")
        for c in chunks
        if isinstance(c.get("content"), str)
    )

    # The status indicator reports "validated". Filter to the content-bearing
    # status chunk (start/end flag delimiters carry no content).
    status_contents = [
        c["content"]
        for c in chunks
        if c.get("type") == "status" and isinstance(c.get("content"), str)
    ]
    assert status_contents, "expected a status/features chunk with content"
    assert any("validated" in content for content in status_contents)


def test_golden_validation_gate_surfaces_errors_on_invalid_code():
    """Invalid code now yields a [Validation] error chunk (execution still
    proceeds — the gate is non-blocking)."""
    interp = _quiet_interpreter()
    interp.enable_validation = True
    interp.auto_run = True
    interp.messages = [
        {"role": "user", "type": "message", "content": "run it"},
        {
            "role": "assistant",
            "type": "code",
            "format": "python",
            "content": "def broken(:\n    pass",  # syntax error
        },
    ]

    def fake_run(language, code, stream=False):
        yield {"type": "console", "format": "output", "content": "ran anyway\n"}

    def fake_llm_run(messages):
        yield {"type": "message", "content": "done"}

    interp.computer.run = fake_run
    interp.llm.run = fake_llm_run

    events = _capture_events()
    chunks = list(interp._respond_and_store())

    event_types = {ev.type for ev in events}
    assert EventType.VALIDATION_START in event_types
    assert EventType.VALIDATION_END in event_types

    assert any(
        "[Validation]" in (c.get("content") or "")
        for c in chunks
        if isinstance(c.get("content"), str)
    ), "invalid code should surface a [Validation] error chunk"
