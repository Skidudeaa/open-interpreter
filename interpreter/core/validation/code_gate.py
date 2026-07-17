"""Pre-execution validation gate, extracted from respond.py.

``CodeGate.check(interpreter, language, code)`` returns
``(validated: bool, error_lines: list[str])`` — ``validated`` drives the
'✓ validated' status flag, ``error_lines`` are console strings the caller yields.

This gate previously did nothing: the inline code called
``syntax_checker.check(language, code)`` with the arguments swapped (the real
signature is ``check(code, file_path, language=None)`` returning a
``SyntaxCheckResult`` dataclass) and then called ``.get("valid")`` on the
dataclass, raising an AttributeError the non-blocking except swallowed — so
``VALIDATION_START`` fired but ``VALIDATION_END`` never did and no ``[Validation]``
lines were produced. That bug is now fixed: we call ``check`` with the correct
arguments and read ``result.valid`` / ``result.errors`` off the dataclass, so
validation actually runs (opt-in via ``enable_validation``). Failures remain
non-blocking (surfaced as ``[Validation]`` console lines; execution still
proceeds).
"""

from __future__ import annotations

import logging

from ...terminal_interface.components.ui_events import EventType, UIEvent, get_event_bus

logger = logging.getLogger(__name__)


class CodeGate:
    """Pre-execution syntax gate."""

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

            # Correct call: check(code, file_path, language=...). file_path is only
            # used to label error locations; this is script execution (no file), so
            # pass "" and set language explicitly.
            validation_result = interpreter.syntax_checker.check(
                code, "", language=language
            )
            validated = True
            is_valid = validation_result.valid
            errors = validation_result.errors

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
