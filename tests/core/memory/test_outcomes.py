"""Tests for outcome memory: message extraction + failure store."""

import pytest

from interpreter.core.memory.outcomes import Outcome, OutcomeStore, extract_outcomes


def _code(c):
    return {"role": "assistant", "type": "code", "format": "python", "content": c}


def _console(c):
    return {"role": "computer", "type": "console", "format": "output", "content": c}


def test_detects_failure_from_traceback():
    msgs = [
        _code("open('x')"),
        _console("Traceback (most recent call last)\nFileNotFoundError: no x"),
    ]
    outcomes = extract_outcomes(msgs)
    assert len(outcomes) == 1
    assert outcomes[0].status == "failure"
    assert "FileNotFoundError: no x" in outcomes[0].signature


def test_detects_success():
    msgs = [_code("print(1)"), _console("1")]
    outcomes = extract_outcomes(msgs)
    assert outcomes[0].status == "success"


def test_no_outcome_without_output():
    assert extract_outcomes([_code("print(1)")]) == []


def test_start_index_skips_earlier_messages():
    msgs = [
        _code("a"),
        _console("NameError: a not defined"),
        _code("b"),
        _console("ok"),
    ]
    outcomes = extract_outcomes(msgs, start_index=2)
    assert len(outcomes) == 1
    assert outcomes[0].status == "success"


@pytest.fixture
def store():
    s = OutcomeStore(db_path=None)
    yield s
    s.close()


def test_record_aggregates_by_signature(store):
    o = Outcome(signature="ValueError: bad", status="failure", error="ValueError: bad")
    store.record(o)
    store.record(o)
    recurring = store.recurring_failures()
    assert len(recurring) == 1
    assert recurring[0]["count"] == 2


def test_successes_not_persisted(store):
    store.record(Outcome(signature="", status="success"))
    assert store.recurring_failures() == []


def test_record_from_messages(store):
    msgs = [
        _code("x"),
        _console("KeyError: 'missing'"),
        _code("y"),
        _console("done"),
    ]
    n = store.record_from_messages(msgs)
    assert n == 1
    recurring = store.recurring_failures()
    assert "KeyError" in recurring[0]["signature"]


def test_min_count_filter(store):
    store.record(Outcome("E1: x", "failure", error="E1: x"))
    store.record(Outcome("E2: y", "failure", error="E2: y"))
    store.record(Outcome("E2: y", "failure", error="E2: y"))
    only_recurring = store.recurring_failures(min_count=2)
    assert len(only_recurring) == 1
    assert only_recurring[0]["signature"] == "E2: y"
