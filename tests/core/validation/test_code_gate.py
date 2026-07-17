"""Unit tests for the extracted CodeGate service.

These pin the CURRENT (buggy) no-op behavior verbatim. Plan Step 6 fixes the gate
and deliberately updates these expectations.
"""

from types import SimpleNamespace

import pytest

from interpreter.core.validation.code_gate import CodeGate
from interpreter.core.validation.syntax_checker import SyntaxCheckResult
from interpreter.terminal_interface.components.ui_events import (
    EventType,
    get_event_bus,
    reset_event_bus,
)


class _DataclassReturningChecker:
    """Mimics the real SyntaxChecker: returns a dataclass (no .get method)."""

    def check(self, *args, **kwargs):
        return SyntaxCheckResult(valid=True, language="python")


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


def test_enabled_is_currently_a_noop_but_sets_validated():
    """VALIDATION_START fires and validated=True, but the swallowed dataclass-.get
    bug means VALIDATION_END never fires and no error lines are produced."""
    interp = SimpleNamespace(
        enable_validation=True, syntax_checker=_DataclassReturningChecker()
    )
    events = _events()

    validated, error_lines = CodeGate().check(interp, "python", "print(1)")

    assert validated is True
    assert error_lines == []
    types = {ev.type for ev in events}
    assert EventType.VALIDATION_START in types
    assert EventType.VALIDATION_END not in types  # THE BUG
