"""Memory pre-prompting in SystemMessageBuilder.

Verifies the dynamic memory preamble: injected when enabled + memories exist,
and a strict no-op (base message unchanged) when disabled/empty/erroring.
"""

from unittest.mock import MagicMock

import pytest

from interpreter.core.services import system_message_builder as smb
from interpreter.core.services.system_message_builder import SystemMessageBuilder


@pytest.fixture(autouse=True)
def _deterministic_static(monkeypatch):
    # Make the static base exactly "BASE PROMPT": no headless append, fresh cache.
    monkeypatch.setattr(smb, "_detect_headless", lambda: False)
    smb._system_message_cache.clear()
    yield
    smb._system_message_cache.clear()


def _interp(enable_preprompt, search_results=None, query="fix the socket"):
    """A minimal interpreter stand-in for build()."""
    it = MagicMock()
    it.system_message = "BASE PROMPT"
    it.custom_instructions = ""
    it.computer.terminal.languages = []
    it.computer.import_computer_api = False
    it.computer.system_message = ""
    it.enable_memory_preprompt = enable_preprompt
    it.memory_preprompt_limit = 5
    it.messages = [{"role": "user", "type": "message", "content": query}]
    it.reranker = None
    if search_results is None:
        it.semantic_graph = None
    else:
        graph = MagicMock()
        graph.semantic_search.return_value = search_results
        it.semantic_graph = graph
    return it


def test_disabled_is_byte_identical_to_static():
    it = _interp(enable_preprompt=False)
    msg = SystemMessageBuilder().build(it)
    assert msg == "BASE PROMPT"  # no preamble, exactly the static base


def test_injects_preamble_when_enabled_with_memories():
    results = [
        {
            "type": "edit",
            "content": "hardened the unix socket perms",
            "score": 0.9,
            "metadata": {"file_path": "server.py"},
        },
        {
            "type": "edit",
            "content": "logged dropped events",
            "score": 0.4,
            "metadata": {},
        },
    ]
    it = _interp(enable_preprompt=True, search_results=results)
    msg = SystemMessageBuilder().build(it)
    assert "Relevant context from past work" in msg
    assert "- hardened the unix socket perms (server.py)" in msg
    assert "- logged dropped events" in msg
    assert msg.endswith("BASE PROMPT")  # preamble precedes the base
    it.semantic_graph.semantic_search.assert_called_once()


def test_no_preamble_when_no_results():
    it = _interp(enable_preprompt=True, search_results=[])
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"


def test_no_preamble_without_semantic_graph():
    it = _interp(enable_preprompt=True, search_results=None)  # graph is None
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"


def test_no_preamble_without_query():
    it = _interp(enable_preprompt=True, search_results=[{"content": "x"}], query="")
    it.messages = []  # no user message
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"


def test_search_error_is_swallowed():
    it = _interp(enable_preprompt=True, search_results=[])
    it.semantic_graph.semantic_search.side_effect = RuntimeError("db down")
    assert SystemMessageBuilder().build(it) == "BASE PROMPT"  # degrades, no raise


def test_reranker_and_limit_forwarded():
    it = _interp(enable_preprompt=True, search_results=[])
    it.reranker = MagicMock()
    it.memory_preprompt_limit = 3
    SystemMessageBuilder().build(it)
    kwargs = it.semantic_graph.semantic_search.call_args.kwargs
    assert kwargs["limit"] == 3
    assert kwargs["reranker"] is it.reranker
