"""Unit tests for the extracted SystemMessageBuilder service."""

import interpreter.core.services.system_message_builder as smb_mod
from interpreter.core.core import OpenInterpreter
from interpreter.core.services.system_message_builder import SystemMessageBuilder


def _interp() -> OpenInterpreter:
    interp = OpenInterpreter()
    interp.enable_agents = False
    interp.enable_semantic_memory = False
    return interp


def test_build_includes_base_and_custom_instructions():
    interp = _interp()
    interp.system_message = "BASE PROMPT"
    interp.custom_instructions = "CUSTOM RULES"

    built = SystemMessageBuilder().build(interp)

    assert "BASE PROMPT" in built
    assert "CUSTOM RULES" in built


def test_build_is_cached_and_returns_identical_string():
    interp = _interp()
    interp.system_message = "CACHE ME"

    builder = SystemMessageBuilder()
    first = builder.build(interp)
    second = SystemMessageBuilder().build(interp)  # fresh instance, shared cache

    # Same content, and the cache returns the very same object on a hit.
    assert first == second
    assert first is second


def test_cache_invalidates_when_dependencies_change():
    interp = _interp()
    interp.system_message = "V1"
    first = SystemMessageBuilder().build(interp)

    interp.system_message = "V2"
    second = SystemMessageBuilder().build(interp)

    assert first != second
    assert "V2" in second


def test_headless_warning_appended_when_headless(monkeypatch):
    monkeypatch.setattr(smb_mod, "_detect_headless", lambda: True)
    interp = _interp()
    interp.system_message = "BASE"

    # Bypass the cache by using a distinct interpreter each run.
    built = SystemMessageBuilder().build(interp)

    assert "HEADLESS environment" in built


def test_computer_api_message_not_duplicated(monkeypatch):
    monkeypatch.setattr(smb_mod, "_detect_headless", lambda: False)
    interp = _interp()
    interp.system_message = "BASE"
    interp.computer.import_computer_api = True
    interp.computer.system_message = "COMPUTER API DOCS"

    built = SystemMessageBuilder().build(interp)

    assert built.count("COMPUTER API DOCS") == 1
