"""Tests for the %reflect escalation command (toggle to a heavier reasoner)."""

from unittest.mock import MagicMock

from interpreter.terminal_interface.magic_commands import handle_reflect

MAIN = "gemini/gemini-3.1-pro-preview"
REFLECT = "openrouter/moonshotai/kimi-k3"


def _it(model=MAIN):
    it = MagicMock()
    it.llm.model = model
    it.reflect_model = REFLECT
    it._reflect_prev_model = None
    return it


def test_engage_swaps_and_stashes_previous():
    it = _it()
    handle_reflect(it, "")
    assert it.llm.model == REFLECT
    assert it._reflect_prev_model == MAIN


def test_toggle_off_reverts_to_previous():
    it = _it()
    handle_reflect(it, "")  # engage
    handle_reflect(it, "")  # toggle off (model == reflect_model, prev set)
    assert it.llm.model == MAIN
    assert it._reflect_prev_model is None


def test_explicit_off_reverts():
    it = _it()
    handle_reflect(it, "")
    handle_reflect(it, "off")
    assert it.llm.model == MAIN
    assert it._reflect_prev_model is None


def test_off_when_not_reflecting_is_noop():
    it = _it()
    handle_reflect(it, "off")
    assert it.llm.model == MAIN
    assert it._reflect_prev_model is None
