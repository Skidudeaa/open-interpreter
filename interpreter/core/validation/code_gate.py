"""Pre-execution validation gate, extracted from respond.py.

``CodeGate.check(interpreter, language, code)`` returns
``(validated: bool, error_lines: list[str])`` — ``validated`` drives the
'✓ validated' status flag, ``error_lines`` are console strings the caller yields.

IMPORTANT — this preserves a PRE-EXISTING BUG verbatim so the decomposition stays
behavior-identical. The gate calls ``syntax_checker.check(language, code)`` with
the arguments swapped (the real signature is ``check(code, file_path,
language=None)`` returning a ``SyntaxCheckResult`` dataclass), then calls
``.get("valid")`` on that dataclass — which has no ``.get`` — raising
AttributeError that the non-blocking except swallows. Net effect TODAY:
``VALIDATION_START`` fires and ``validated`` is set True, but ``VALIDATION_END``
never fires and no ``[Validation]`` error lines are ever produced.

The real fix (route through ``syntax_checker.check(code, file_path=...,
language=...)`` / ``EditValidator`` and read ``result.valid``/``result.errors``
off the dataclass) is a deliberate, separately-approved behavior change — see the
respond-decomposition plan Step 6 — not part of this extraction.
"""

from __future__ import annotations

import logging

from ...terminal_interface.components.ui_events import EventType, UIEvent, get_event_bus

logger = logging.getLogger(__name__)


class CodeGate:
    """Pre-execution syntax gate (currently a no-op; see module docstring)."""

    def check(self, interpreter, language: str, code: str) -> tuple[bool, list[str]]:
        validated = False
        error_lines: list[str] = []

        if not (interpreter.enable_validation and interpreter.syntax_checker):
            return validated, error_lines

        try:
            # Emit start event for UI feedback
            event_bus = get_event_bus()
            event_bus.emit(
                UIEvent(
                    type=EventType.VALIDATION_START,
                    data={"language": language, "code_length": len(code)},
                    source="respond",
                )
            )

            # BUG (preserved): args are swapped and .get() is called on a dataclass;
            # this raises and is swallowed below. See module docstring / plan Step 6.
            validation_result = interpreter.syntax_checker.check(language, code)
            validated = True
            is_valid = validation_result.get("valid", True)
            errors = validation_result.get("errors", [])

            # Emit end event with result
            event_bus.emit(
                UIEvent(
                    type=EventType.VALIDATION_END,
                    data={"valid": is_valid, "error_count": len(errors)},
                    source="respond",
                )
            )

            if not is_valid:
                for error in errors:
                    error_lines.append(f"[Validation] {error}\n")
        except Exception as e:
            logger.debug(f"Validation failed (non-blocking): {e}")

        return validated, error_lines
