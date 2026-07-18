"""The conversational fast-path in _detect_workflow avoids a per-turn LLM call."""

from unittest.mock import MagicMock

import pytest

from interpreter.core.agents.orchestrator import AgentOrchestrator, WorkflowType


@pytest.mark.parametrize(
    "text",
    [
        "yes, let's go with that approach",
        "ok that makes sense to me",
        "sounds good, thanks so much",
        "great work on this",
        "sure, please continue with the plan",
        "hmm, interesting point about that",
    ],
)
def test_clearly_conversational_true(text):
    assert AgentOrchestrator._is_clearly_conversational(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "find the config file",
        "where is auth handled",
        "fix the login bug please",
        "what files use the reranker",  # has "file" signal
        "implement the new feature",
        "what do you think about the layout",  # opens with 'what' -> defer, not skip
    ],
)
def test_not_conversational_defers(text):
    assert AgentOrchestrator._is_clearly_conversational(text) is False


def test_detect_workflow_skips_llm_on_conversation():
    interp = MagicMock()
    interp.llm.run = MagicMock(side_effect=AssertionError("LLM must not be called"))
    orch = AgentOrchestrator(interp)
    wf = orch._detect_workflow("yes, let's proceed with that whole approach")
    assert wf == WorkflowType.NONE
    interp.llm.run.assert_not_called()


def test_detect_workflow_still_calls_llm_when_ambiguous():
    interp = MagicMock()
    # a code-ish message with no clear heuristic route -> must reach the LLM
    called = {"n": 0}

    def _run(messages):
        called["n"] += 1
        yield {"type": "message", "content": "NONE"}

    interp.llm.run = _run
    orch = AgentOrchestrator(interp)
    orch._detect_workflow("could you take a look at the authentication approach")
    assert called["n"] == 1  # ambiguous -> classifier ran
