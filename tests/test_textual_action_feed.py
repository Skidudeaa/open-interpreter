"""Regression tests for Textual action visibility."""

import pytest

from interpreter.core.core import OpenInterpreter


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


def test_action_feed_keeps_recent_actions_in_order():
    """The Textual action feed should render the latest actions oldest to newest."""
    from interpreter.terminal_interface.widgets.action_feed import ActionFeedWidget

    feed = ActionFeedWidget(max_actions=3)

    feed.add_action("Request submitted")
    feed.add_action("Code proposed", "python")
    feed.add_action("Approval required", "python")
    feed.add_action("Execution skipped")

    rendered = feed.render().plain

    assert "Request submitted" not in rendered
    assert rendered.index("Code proposed") < rendered.index("Approval required")
    assert rendered.index("Approval required") < rendered.index("Execution skipped")
    assert "python" in rendered


def test_action_feed_deduplicates_adjacent_actions():
    """Duplicate signals from chunk and EventBus paths should not crowd the feed."""
    from interpreter.terminal_interface.widgets.action_feed import ActionFeedWidget

    feed = ActionFeedWidget(max_actions=5)

    feed.add_action("Code proposed", "python")
    feed.add_action("Code proposed", "python")
    feed.add_action("Approval required", "python")

    rendered = feed.render().plain

    assert rendered.count("Code proposed") == 1
    assert rendered.count("Approval required") == 1


@pytest.mark.asyncio
async def test_textual_action_feed_mounts_and_toggles_in_live_app():
    """The mounted Textual app should expose a collapsible action feed."""
    from interpreter.terminal_interface.components.ui_state import UIMode
    from interpreter.terminal_interface.textual_app import InterpreterTUI
    from interpreter.terminal_interface.widgets.action_feed import ActionFeedWidget

    app = InterpreterTUI(_quiet_interpreter())

    async with app.run_test() as pilot:
        feed = app.query_one("#action-feed", ActionFeedWidget)
        app._record_action("Smoke action", "mounted")
        await pilot.pause()

        assert "Smoke action" in feed.render().plain
        assert not feed.has_class("hidden")

        app.action_toggle_action_feed()
        await pilot.pause()
        assert feed.has_class("hidden")

        app.action_toggle_action_feed()
        await pilot.pause()
        assert not feed.has_class("hidden")

        app.ui_mode = UIMode.ZEN
        app._update_mode_class()
        await pilot.pause()
        assert feed.has_class("hidden")


def test_textual_process_chunk_records_confirmation_action():
    """Confirmation chunks should surface a visible pending action before waiting."""
    from interpreter.terminal_interface.textual_app import InterpreterTUI

    interp = _quiet_interpreter()
    interp.auto_run = False

    app = object.__new__(InterpreterTUI)
    app.interpreter = interp
    actions = []

    def fake_call_from_thread(func, *args):
        func(*args)

    def fake_request_confirmation(code_info, decision=None, decision_event=None):
        interp._code_execution_approved = True
        if decision is not None:
            decision["approved"] = True
        if decision_event is not None:
            decision_event.set()

    app.call_from_thread = fake_call_from_thread
    app._request_confirmation = fake_request_confirmation
    app._record_action = lambda message, detail="": actions.append((message, detail))

    app._process_chunk(
        {
            "type": "confirmation",
            "content": {"format": "python", "content": "print('run')"},
        }
    )

    assert actions == [("Approval required", "python")]
    assert interp._code_execution_approved is True


def test_textual_agent_output_event_records_action():
    """Agent output events should be visible in the Textual action feed."""
    from interpreter.terminal_interface.components.ui_events import EventType, UIEvent
    from interpreter.terminal_interface.textual_app import InterpreterTUI

    app = object.__new__(InterpreterTUI)
    actions = []

    app.call_from_thread = lambda func, *args: func(*args)
    app._record_action = lambda message, detail="": actions.append((message, detail))

    app._on_agent_output(
        UIEvent(
            type=EventType.AGENT_OUTPUT,
            data={"role": "scout", "message": "found useful files"},
        )
    )

    assert actions == [("Agent update", "scout: found useful files")]


def test_textual_activity_event_records_action():
    """Existing activity events should land in the Textual action feed."""
    from interpreter.terminal_interface.components.ui_events import EventType, UIEvent
    from interpreter.terminal_interface.textual_app import InterpreterTUI

    app = object.__new__(InterpreterTUI)
    actions = []

    app.call_from_thread = lambda func, *args: func(*args)
    app._record_action = lambda message, detail="": actions.append((message, detail))

    app._on_activity(
        UIEvent(
            type=EventType.ACTIVITY,
            data={
                "activity_type": "validate",
                "message": "Running validation",
                "context": "pytest",
            },
        )
    )

    assert actions == [("Running validation", "pytest")]
