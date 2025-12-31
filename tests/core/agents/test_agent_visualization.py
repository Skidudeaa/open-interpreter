"""
Test script for agent visualization.

Run with: python tests/core/agents/test_agent_visualization.py

This tests that agent status is visually displayed during orchestration.
"""

import tempfile
from pathlib import Path

from rich.console import Console


def test_visual_agent_tracking():
    """Test that agent events are emitted and can be visualized."""
    from interpreter.core.agents.orchestrator import WorkflowType
    from interpreter.core.core import OpenInterpreter
    from interpreter.terminal_interface.components.ui_events import (
        EventType,
        get_event_bus,
    )

    console = Console()
    console.print("\n[bold cyan]Agent Visualization Test[/bold cyan]\n")

    # Create interpreter with agents enabled
    interp = OpenInterpreter()
    interp.enable_agents = True

    # Create UI state and event tracking
    events_received = []

    # Subscribe to agent events
    event_bus = get_event_bus()

    def track_event(event):
        events_received.append(event)
        # Display event as it happens
        if event.type == EventType.AGENT_SPAWN:
            role = event.data.get("role", "unknown")
            console.print(
                f"  [dim]▶[/dim] [cyan]🤖 {role.title()}[/cyan] [dim]spawned[/dim]"
            )
        elif event.type == EventType.AGENT_COMPLETE:
            role = event.data.get("role", "unknown")
            console.print(
                f"  [green]✓[/green] [cyan]🤖 {role.title()}[/cyan] [dim]complete[/dim]"
            )
        elif event.type == EventType.AGENT_ERROR:
            role = event.data.get("role", "unknown")
            error = event.data.get("error", "")
            console.print(
                f"  [red]✗[/red] [cyan]🤖 {role.title()}[/cyan] [red]{error[:30]}[/red]"
            )

    event_bus.subscribe(EventType.AGENT_SPAWN, track_event)
    event_bus.subscribe(EventType.AGENT_COMPLETE, track_event)
    event_bus.subscribe(EventType.AGENT_ERROR, track_event)

    # Create temp project
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "main.py").write_text("def main(): pass")
        Path(tmpdir, "utils.py").write_text("class Helper: pass")

        # Get orchestrator
        orch = interp.agent_orchestrator
        orch.root_path = tmpdir

        console.print("[bold]Running EXPLORE workflow...[/bold]")
        result = orch.handle_task(
            'find all Python files matching "*.py" in the project',
            workflow=WorkflowType.EXPLORE,
        )
        console.print(f"  Result: {'✓' if result.success else '✗'}")
        console.print(f"  Duration: {result.total_duration_ms:.0f}ms")
        console.print()

        console.print("[bold]Running EDIT workflow...[/bold]")
        result = orch.handle_task(
            'add a method to the Helper class in "utils.py"',
            workflow=WorkflowType.EDIT,
        )
        console.print(f"  Result: {'✓' if result.success else '✗'}")
        console.print(f"  Duration: {result.total_duration_ms:.0f}ms")
        console.print()

        console.print("[bold]Running VALIDATE workflow...[/bold]")
        result = orch.handle_task(
            "validate the Python syntax in main.py file",
            workflow=WorkflowType.VALIDATE,
        )
        console.print(f"  Result: {'✓' if result.success else '✗'}")
        console.print(f"  Duration: {result.total_duration_ms:.0f}ms")
        console.print()

    # Summary
    console.print(f"[bold green]Events received: {len(events_received)}[/bold green]")

    spawn_events = [e for e in events_received if e.type == EventType.AGENT_SPAWN]
    complete_events = [e for e in events_received if e.type == EventType.AGENT_COMPLETE]
    error_events = [e for e in events_received if e.type == EventType.AGENT_ERROR]

    console.print(f"  SPAWN: {len(spawn_events)}")
    console.print(f"  COMPLETE: {len(complete_events)}")
    console.print(f"  ERROR: {len(error_events)}")

    # Assertions for test
    assert len(spawn_events) > 0, "Expected at least one AGENT_SPAWN event"
    console.print("\n[bold green]✓ Agent visualization test passed![/bold green]")

    return True


def test_live_tracker_component():
    """Test the LiveAgentTracker component directly."""
    from interpreter.terminal_interface.components.live_agent_tracker import (
        SimpleAgentDisplay,
    )
    from interpreter.terminal_interface.components.ui_state import (
        AgentRole,
        AgentStatus,
        UIState,
    )

    console = Console()
    console.print("\n[bold cyan]LiveAgentTracker Component Test[/bold cyan]\n")

    # Create UI state with some agents
    state = UIState()
    state.add_agent("scout-1", AgentRole.SCOUT)
    state.add_agent("surgeon-1", AgentRole.SURGEON)

    # Update statuses
    state.update_agent_status("scout-1", AgentStatus.RUNNING)

    # Test SimpleAgentDisplay
    display = SimpleAgentDisplay(state, console)
    line = display.render_line()
    console.print(f"Rendered line: {line}")

    # Names are truncated to 4 chars: Scou, Surg
    assert "Scou" in line, f"Expected 'Scou' in output: {line}"
    assert "Surg" in line, f"Expected 'Surg' in output: {line}"

    console.print("[bold green]✓ LiveAgentTracker component test passed![/bold green]")
    return True


if __name__ == "__main__":
    test_live_tracker_component()
    print()
    test_visual_agent_tracking()
