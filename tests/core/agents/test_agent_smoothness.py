"""Regression tests for smooth, predictable agent lifecycle events."""

from unittest.mock import MagicMock

from interpreter.core.agents.base_agent import AgentResult, AgentRole
from interpreter.core.agents.orchestrator import AgentOrchestrator
from interpreter.core.core import OpenInterpreter
from interpreter.terminal_interface.components.ui_events import EventType


class FakeAgent:
    def __init__(self, result: AgentResult):
        self.result = result

    def run(self, task, context=None):
        return self.result


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


def _emitted_events(bus):
    return [call.args[0] for call in bus.emit.call_args_list]


def test_agent_lifecycle_emitter_shapes_payloads_consistently(tmp_path):
    """The lifecycle emitter should centralize status, timing, and lineage fields."""
    from interpreter.core.agents.orchestrator import AgentLifecycleEmitter

    bus = MagicMock()
    orchestrator = AgentOrchestrator(
        interpreter=_quiet_interpreter(),
        root_path=str(tmp_path),
        event_bus=bus,
    )
    emitter = AgentLifecycleEmitter(
        orchestrator=orchestrator,
        agent_id="scout-42",
        role=AgentRole.SCOUT,
        task="find useful files",
        parent_id="root-agent",
    )

    emitter.spawn()
    emitter.output("found useful files")
    emitter.complete(AgentResult(role=AgentRole.SCOUT, success=True, output="done"))

    events = _emitted_events(bus)

    assert [event.type for event in events] == [
        EventType.AGENT_SPAWN,
        EventType.AGENT_OUTPUT,
        EventType.AGENT_COMPLETE,
    ]
    for event in events:
        assert event.data["agent_id"] == "scout-42"
        assert event.data["role"] == "scout"
        assert event.data["task"] == "find useful files"
        assert event.data["parent_id"] == "root-agent"
        assert "started_at" in event.data
        assert event.data["elapsed_ms"] >= 0


def test_agent_lifecycle_events_have_stable_status_payloads(tmp_path):
    """Agent runs should surface running, output, and completion with timing."""
    bus = MagicMock()
    orchestrator = AgentOrchestrator(
        interpreter=_quiet_interpreter(),
        root_path=str(tmp_path),
        event_bus=bus,
    )
    orchestrator._agents[AgentRole.SCOUT] = FakeAgent(
        AgentResult(
            role=AgentRole.SCOUT,
            success=True,
            output="found useful files",
            content={"files": ["main.py"]},
        )
    )

    agent_id, result = orchestrator._execute_agent_with_events(
        AgentRole.SCOUT,
        "find useful files",
        parent_id="root-agent",
    )

    events = _emitted_events(bus)
    event_types = [event.type for event in events]

    assert result.success is True
    assert event_types == [
        EventType.AGENT_SPAWN,
        EventType.AGENT_OUTPUT,
        EventType.AGENT_COMPLETE,
    ]
    for event in events:
        assert event.data["agent_id"] == agent_id
        assert event.data["role"] == "scout"
        assert event.data["task"] == "find useful files"
        assert event.data["parent_id"] == "root-agent"
        assert "started_at" in event.data
        assert "elapsed_ms" in event.data

    assert events[0].data["status"] == "running"
    assert events[1].data["status"] == "running"
    assert events[1].data["message"] == "found useful files"
    assert events[2].data["status"] == "complete"
    assert events[2].data["elapsed_ms"] >= 0


def test_agent_error_event_uses_agent_result_error(tmp_path):
    """Failed agent results should emit their specific error, not a generic one."""
    bus = MagicMock()
    orchestrator = AgentOrchestrator(
        interpreter=_quiet_interpreter(),
        root_path=str(tmp_path),
        event_bus=bus,
    )
    orchestrator._agents[AgentRole.SURGEON] = FakeAgent(
        AgentResult(
            role=AgentRole.SURGEON,
            success=False,
            error="patch did not apply",
        )
    )

    _, result = orchestrator._execute_agent_with_events(
        AgentRole.SURGEON,
        "fix modal",
    )

    events = _emitted_events(bus)

    assert result.success is False
    assert [event.type for event in events] == [
        EventType.AGENT_SPAWN,
        EventType.AGENT_ERROR,
    ]
    assert events[-1].data["status"] == "error"
    assert events[-1].data["error"] == "patch did not apply"
    assert events[-1].data["elapsed_ms"] >= 0
