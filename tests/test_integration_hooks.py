"""
Integration test for the new hooks in respond.py.
Tests the actual execution flow with file detection and hooks.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_file_detection_in_respond():
    """Test that file changes are detected during code execution."""
    from interpreter.core.core import OpenInterpreter

    with tempfile.TemporaryDirectory() as tmpdir:
        interp = OpenInterpreter()
        interp.activate_all_features()
        interp.auto_run = True
        interp.llm.model = "gpt-3.5-turbo"  # Just need a model name
        interp.computer.cwd = tmpdir

        # Create a test file first
        test_file = Path(tmpdir) / "test.py"
        test_file.write_text("# original")

        # Capture pre-execution state
        from interpreter.core.utils.file_snapshot import capture_source_file_states

        before = capture_source_file_states(tmpdir)
        assert str(test_file) in before, "test.py should be captured"

        # Simulate file modification (as if code execution did it)
        test_file.write_text("# modified by code")

        # Capture post-execution state
        from interpreter.core.utils.file_snapshot import diff_file_states

        after = capture_source_file_states(tmpdir)
        changed = diff_file_states(before, after)

        assert len(changed) == 1, f"Expected 1 changed file, got {len(changed)}"
        old, new = changed[str(test_file)]
        assert old == "# original", f"Wrong old: {old}"
        assert new == "# modified by code", f"Wrong new: {new}"

        print("✓ test_file_detection_in_respond passed")


# NOTE: The four hook tests below previously grepped respond.py for literal
# comment banners ("AUTO-TEST HOOK", "TRACE FEEDBACK TO LLM", ...). That coupled
# them to comment text and would false-fail when the decomposition relocates the
# inline hooks into module-level helpers. They now assert on the stable wiring
# instead: the feature flag exists on a real interpreter and the underlying
# machinery imports and is callable. The behavioral chunk-sequence contract is
# covered by tests/core/test_respond_golden.py.


def test_status_dict_includes_tested():
    """The auto-test feature is wired and its status flag is a real interpreter
    attribute (the status indicator surfaces '✓ tested')."""
    from interpreter.core.core import OpenInterpreter

    interp = OpenInterpreter()
    interp.activate_all_features()
    assert hasattr(interp, "enable_auto_test"), "auto-test flag should be wired"
    assert interp.enable_auto_test is True

    print("✓ test_status_dict_includes_tested passed")


def test_trace_feedback_hook_exists():
    """Trace-feedback machinery is importable and the feature flag is wired."""
    from interpreter.core.core import OpenInterpreter
    from interpreter.core.tracing import TraceContextGenerator

    assert callable(TraceContextGenerator)

    interp = OpenInterpreter()
    interp.activate_all_features()
    assert hasattr(interp, "enable_trace_feedback"), "trace-feedback flag wired"
    assert hasattr(interp, "enable_tracing"), "tracing flag wired"

    print("✓ test_trace_feedback_hook_exists passed")


def test_auto_test_hook_exists():
    """Auto-test machinery is importable and the feature flag is wired."""
    from interpreter.core.core import OpenInterpreter
    from interpreter.core.validation.test_discovery import TestDiscovery

    assert callable(TestDiscovery)

    interp = OpenInterpreter()
    interp.activate_all_features()
    assert hasattr(interp, "enable_auto_test"), "auto-test flag wired"

    print("✓ test_auto_test_hook_exists passed")


def test_file_snapshot_hook_exists():
    """File-change detection machinery is importable and behaves (round-trips a
    real edit through capture -> diff)."""
    from interpreter.core.utils.file_snapshot import (
        capture_source_file_states,
        diff_file_states,
    )

    assert callable(capture_source_file_states)
    assert callable(diff_file_states)

    with tempfile.TemporaryDirectory() as tmpdir:
        f = Path(tmpdir) / "snap.py"
        f.write_text("# before")
        before = capture_source_file_states(tmpdir)
        f.write_text("# after")
        after = capture_source_file_states(tmpdir)
        changed = diff_file_states(before, after)
        assert str(f) in changed
        assert changed[str(f)] == ("# before", "# after")

    print("✓ test_file_snapshot_hook_exists passed")


def run_integration_tests():
    """Run all integration tests."""
    print("=" * 60)
    print("Integration Tests for New Hooks")
    print("=" * 60)

    tests = [
        test_file_detection_in_respond,
        test_status_dict_includes_tested,
        test_trace_feedback_hook_exists,
        test_auto_test_hook_exists,
        test_file_snapshot_hook_exists,
    ]

    passed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"✗ {test.__name__} FAILED: {e}")

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{len(tests)} integration tests passed")
    print("=" * 60)

    return passed == len(tests)


if __name__ == "__main__":
    success = run_integration_tests()
    sys.exit(0 if success else 1)
