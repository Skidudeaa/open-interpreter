"""Tests for the always-on structured session logger."""

import json

import pytest

from interpreter.core.session_log import SessionLogger, current_caller_role, sha8


def _read(path):
    return [json.loads(line) for line in open(path)]


def test_envelope_and_provenance(tmp_path):
    log = SessionLogger(session_id="t1", log_dir=tmp_path)
    log.log("thing", foo="bar")
    log.close()
    (rec,) = _read(log.path)
    # Envelope present on every record.
    for key in ("ts", "epoch_ms", "session_id", "seq", "oi_version", "git_sha", "kind"):
        assert key in rec
    assert rec["session_id"] == "t1"
    assert rec["kind"] == "thing"
    assert rec["foo"] == "bar"


def test_seq_is_monotonic(tmp_path):
    log = SessionLogger(session_id="t2", log_dir=tmp_path)
    for i in range(5):
        log.log("n", i=i)
    log.close()
    seqs = [r["seq"] for r in _read(log.path)]
    assert seqs == [1, 2, 3, 4, 5]


def test_disabled_writes_nothing(tmp_path):
    log = SessionLogger(session_id="t3", log_dir=tmp_path, enabled=False)
    log.log("thing", foo="bar")
    log.close()
    assert log.path is None


def test_env_disable(monkeypatch, tmp_path):
    monkeypatch.setenv("OI_SESSION_LOG", "0")
    log = SessionLogger(session_id="t4", log_dir=tmp_path, enabled=True)
    assert log.enabled is False


def test_llm_request_record_captures_role_and_params(tmp_path):
    log = SessionLogger(session_id="t5", log_dir=tmp_path)
    token = current_caller_role.set("surgeon")
    try:
        req = log.llm_request_begin(
            model="gpt-5.6-terra",
            params={"temperature": 0.2, "max_tokens": 4000},
            system_message="You are the Surgeon.",
            n_messages=7,
        )
        req.first_token()
        req.observe_chunk({"finish_reason": "stop", "usage": {"prompt_tokens": 100}})
        req.end(status="ok")
    finally:
        current_caller_role.reset(token)
    log.close()

    (rec,) = _read(log.path)
    assert rec["kind"] == "llm_request"
    assert rec["model"] == "gpt-5.6-terra"
    assert rec["caller_role"] == "surgeon"
    assert rec["params"]["temperature"] == 0.2
    assert rec["n_messages"] == 7
    assert rec["status"] == "ok"
    assert rec["ttft_ms"] is not None
    assert rec["total_ms"] >= 0
    assert rec["finish_reason"] == "stop"
    assert rec["usage"]["prompt_tokens"] == 100
    assert rec["system_prompt_sha"] == sha8("You are the Surgeon.")


def test_llm_request_error_status(tmp_path):
    log = SessionLogger(session_id="t6", log_dir=tmp_path)
    req = log.llm_request_begin(model="m")
    req.end(status="error", error="boom")
    log.close()
    (rec,) = _read(log.path)
    assert rec["status"] == "error"
    assert rec["error"] == "boom"


def test_caller_role_defaults_to_main(tmp_path):
    log = SessionLogger(session_id="t7", log_dir=tmp_path)
    req = log.llm_request_begin(model="m")
    req.end()
    log.close()
    (rec,) = _read(log.path)
    assert rec["caller_role"] == "main"


class _FakeEvent:
    def __init__(self, name, data):
        self.type = type("T", (), {"name": name})()
        self.data = data


def test_event_sink_coalesces_message_chunks(tmp_path):
    log = SessionLogger(session_id="t8", log_dir=tmp_path)
    log._msg_buf = []
    log._code_buf = []
    log._console_buf = []
    log._on_ui_event(_FakeEvent("MESSAGE_START", {}))
    log._on_ui_event(_FakeEvent("MESSAGE_CHUNK", {"content": "Hel"}))
    log._on_ui_event(_FakeEvent("MESSAGE_CHUNK", {"content": "lo"}))
    log._on_ui_event(_FakeEvent("MESSAGE_END", {}))
    log.close()

    recs = [r for r in _read(log.path) if r["kind"] == "terminal_output"]
    assert len(recs) == 1
    assert recs[0]["channel"] == "message"
    assert recs[0]["content"] == "Hello"


def test_event_sink_flushes_console_then_logs_agent(tmp_path):
    log = SessionLogger(session_id="t9", log_dir=tmp_path)
    log._msg_buf = []
    log._code_buf = []
    log._console_buf = []
    log._on_ui_event(_FakeEvent("CONSOLE_OUTPUT", {"content": "a"}))
    log._on_ui_event(_FakeEvent("CONSOLE_OUTPUT", {"content": "b"}))
    # A non-console event flushes the console buffer first.
    log._on_ui_event(
        _FakeEvent("AGENT_SPAWN", {"agent_id": "scout-1", "role": "scout"})
    )
    log.close()

    recs = _read(log.path)
    kinds = [r["kind"] for r in recs]
    assert "terminal_output" in kinds
    console = next(r for r in recs if r["kind"] == "terminal_output")
    assert console["content"] == "ab"
    agent = next(r for r in recs if r["kind"] == "agent_event")
    assert agent["event"] == "AGENT_SPAWN"
    assert agent["agent_id"] == "scout-1"


def test_logging_never_raises_on_bad_data(tmp_path):
    log = SessionLogger(session_id="t10", log_dir=tmp_path)
    # Non-serializable object still writes a line via default=str.
    log.log("weird", obj=object())
    log.close()
    recs = _read(log.path)
    assert recs[0]["kind"] == "weird"
