"""Hermes conversation continuity: the prompt carries prior turns."""

from types import SimpleNamespace

from interpreter.core.backends.hermes_backend import (
    _compose_prompt,
    _conversation_transcript,
)


def _msgs(*pairs):
    return [{"role": r, "type": "message", "content": c} for r, c in pairs]


def test_transcript_excludes_current_request():
    it = SimpleNamespace(
        messages=_msgs(
            ("user", "hi there"), ("assistant", "hello"), ("user", "what now")
        )
    )
    t = _conversation_transcript(it)
    assert "User: hi there" in t
    assert "Assistant: hello" in t
    assert "what now" not in t  # the current request is excluded from history


def test_transcript_empty_for_single_message():
    it = SimpleNamespace(messages=_msgs(("user", "only message")))
    assert _conversation_transcript(it) == ""


def test_compose_includes_history_and_current():
    it = SimpleNamespace(
        messages=_msgs(
            ("user", "my codename is DARKROAST-7"),
            ("assistant", "ok"),
            ("user", "what is my codename"),
        )
    )
    p = _compose_prompt(it)  # SystemMessageBuilder unavailable -> transcript path
    assert "# Conversation so far" in p
    assert "User: my codename is DARKROAST-7" in p
    assert "Assistant: ok" in p
    assert "what is my codename" in p.split("# Current request")[1]


def test_compose_single_message_has_no_history_section():
    it = SimpleNamespace(messages=_msgs(("user", "hello world")))
    p = _compose_prompt(it)
    assert "# Conversation so far" not in p
    assert "hello world" in p


def test_transcript_truncates_long_history():
    pairs = [("user", "x" * 200), ("assistant", "y" * 200)] * 40
    it = SimpleNamespace(messages=_msgs(*pairs, ("user", "current")))
    t = _conversation_transcript(it, max_chars=1000)
    assert t.startswith("…")
    assert len(t) <= 1002  # 1000 + the "…\n" marker
