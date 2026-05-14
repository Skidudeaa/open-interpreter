"""Regression tests for agent execution flow and code approval gating."""

import threading

from interpreter.core.core import OpenInterpreter
from interpreter.core.agents.orchestrator import AgentOrchestrator, WorkflowType
from interpreter.core.respond import respond


class ExplodingOrchestrator:
    """Fails if respond() tries to route an internal agent prompt."""

    def __init__(self):
        self.detect_calls = []

    def _detect_workflow(self, task):
        self.detect_calls.append(task)
        raise AssertionError(f"unexpected workflow detection for: {task}")


def _quiet_interpreter() -> OpenInterpreter:
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


def test_internal_agent_call_bypasses_agent_routing():
    """Agent-owned LLM prompts should not recursively invoke the orchestrator."""
    interp = _quiet_interpreter()
    interp.enable_agents = True
    interp._agent_internal_call = True
    orchestrator = ExplodingOrchestrator()
    interp._agent_orchestrator = orchestrator
    interp.messages = [
        {
            "role": "user",
            "type": "message",
            "content": "## Task\nGenerate edit proposals for this internal agent call.",
        }
    ]

    def fake_llm_run(messages):
        yield {"type": "message", "content": "internal response"}

    interp.llm.run = fake_llm_run

    chunks = list(respond(interp))

    assert orchestrator.detect_calls == []
    assert any(chunk.get("content") == "internal response" for chunk in chunks)


def test_declined_code_confirmation_prevents_computer_run():
    """A UI decline after the confirmation chunk must stop code execution."""
    interp = _quiet_interpreter()
    interp.auto_run = False
    interp.messages = [
        {
            "role": "assistant",
            "type": "code",
            "format": "python",
            "content": "print('should not run')",
        }
    ]

    run_calls = []

    def fake_run(language, code, stream=False):
        run_calls.append((language, code, stream))
        yield {"type": "console", "format": "output", "content": "ran\n"}

    def fake_llm_run(messages):
        yield {"type": "message", "content": "I will avoid running that code."}

    interp.computer.run = fake_run
    interp.llm.run = fake_llm_run

    gen = respond(interp)
    first_chunk = next(gen)
    assert first_chunk["type"] == "confirmation"

    interp._code_execution_approved = False
    next_chunk = next(gen)
    gen.close()

    assert run_calls == []
    assert interp.messages[-1]["role"] == "user"
    assert "declined to run this code" in interp.messages[-1]["content"]
    assert "avoid running" in next_chunk.get("content", "")


def test_obvious_explore_request_uses_heuristic_without_llm():
    """Clear file/code search requests should route without classifier latency."""
    interp = _quiet_interpreter()

    def fail_if_called(messages):
        raise AssertionError("workflow classifier LLM should not be called")
        yield

    interp.llm.run = fail_if_called
    orchestrator = AgentOrchestrator(interp, root_path=".")

    workflow = orchestrator._detect_workflow(
        "find all Python files in the project structure"
    )

    assert workflow == WorkflowType.EXPLORE


def test_textual_confirmation_waits_for_modal_decision():
    """Textual confirmation must block the chat worker until the modal resolves."""
    from interpreter.terminal_interface.textual_app import InterpreterTUI

    interp = _quiet_interpreter()
    interp.auto_run = False

    app = object.__new__(InterpreterTUI)
    app.interpreter = interp

    request_started = threading.Event()
    allow_decision = threading.Event()
    returned = threading.Event()

    def fake_call_from_thread(func, *args):
        thread = threading.Thread(target=func, args=args)
        thread.start()

    def fake_request_confirmation(code_info, decision=None, decision_event=None):
        request_started.set()
        assert code_info["format"] == "python"
        assert "print" in code_info["content"]
        allow_decision.wait(timeout=1)
        interp._code_execution_approved = False
        if decision is not None:
            decision["approved"] = False
        if decision_event is not None:
            decision_event.set()

    app.call_from_thread = fake_call_from_thread
    app._request_confirmation = fake_request_confirmation

    def process_confirmation():
        app._process_chunk(
            {
                "type": "confirmation",
                "content": {"format": "python", "content": "print('skip')"},
            }
        )
        returned.set()

    worker = threading.Thread(target=process_confirmation)
    worker.start()

    assert request_started.wait(timeout=1)
    assert not returned.wait(timeout=0.1)

    allow_decision.set()

    assert returned.wait(timeout=1)
    assert interp._code_execution_approved is False
