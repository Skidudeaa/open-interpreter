"""Unit tests for the CodeGate service (post Step-6 fix: the gate actually
validates)."""

from types import SimpleNamespace

import pytest

from interpreter.core.validation.code_gate import CodeGate
from interpreter.core.validation.syntax_checker import SyntaxChecker
from interpreter.terminal_interface.components.ui_events import (
    EventType,
    get_event_bus,
    reset_event_bus,
)


@pytest.fixture(autouse=True)
def _bus():
    reset_event_bus()
    yield
    reset_event_bus()


def _events():
    captured = []
    get_event_bus().subscribe_all(lambda ev: captured.append(ev))
    return captured


def test_disabled_returns_not_validated_no_errors():
    interp = SimpleNamespace(enable_validation=False, syntax_checker=None)
    assert CodeGate().check(interp, "python", "print(1)") == (False, [])


def test_no_syntax_checker_returns_not_validated():
    interp = SimpleNamespace(enable_validation=True, syntax_checker=None)
    assert CodeGate().check(interp, "python", "print(1)") == (False, [])


def test_valid_code_validates_with_no_errors():
    """Both start and end events fire; valid code yields no error lines."""
    interp = SimpleNamespace(enable_validation=True, syntax_checker=SyntaxChecker())
    events = _events()

    validated, error_lines = CodeGate().check(interp, "python", "print(1)")

    assert validated is True
    assert error_lines == []
    types = {ev.type for ev in events}
    assert EventType.VALIDATION_START in types
    assert EventType.VALIDATION_END in types


def test_invalid_code_surfaces_error_lines():
    """Invalid syntax produces [Validation] error lines and fires VALIDATION_END."""
    interp = SimpleNamespace(enable_validation=True, syntax_checker=SyntaxChecker())
    events = _events()

    validated, error_lines = CodeGate().check(
        interp, "python", "def broken(:\n    pass"
    )

    assert validated is True
    assert error_lines, "invalid code should produce error lines"
    assert all(line.startswith("[Validation] ") for line in error_lines)
    types = {ev.type for ev in events}
    assert EventType.VALIDATION_END in types
