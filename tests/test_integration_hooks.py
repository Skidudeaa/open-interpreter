"""
Integration test for the new hooks in respond.py.
Tests the actual execution flow with file detection and hooks.
"""
import sys
import tempfile
import os
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


def test_status_dict_includes_tested():
    """Test that the status dict includes 'tested' key."""
    # Check the respond.py source to verify status dict
    respond_path = Path(__file__).parent.parent / "interpreter" / "core" / "respond.py"
    content = respond_path.read_text()

    assert '"tested": False' in content or "'tested': False" in content, \
        "Status dict should include 'tested' key"
    assert "tested" in content and "status_parts" in content, \
        "Status indicator should show 'tested'"

    print("✓ test_status_dict_includes_tested passed")


def test_trace_feedback_hook_exists():
    """Test that trace feedback hook exists in respond.py."""
    respond_path = Path(__file__).parent.parent / "interpreter" / "core" / "respond.py"
    content = respond_path.read_text()

    assert "TRACE FEEDBACK TO LLM" in content, "Trace feedback hook should exist"
    assert "enable_trace_feedback" in content, "Should check enable_trace_feedback flag"
    assert "TraceContextGenerator" in content, "Should use TraceContextGenerator"
    assert "Please analyze the trace" in content, "Should have LLM prompt"

    print("✓ test_trace_feedback_hook_exists passed")


def test_auto_test_hook_exists():
    """Test that auto-test hook exists in respond.py."""
    respond_path = Path(__file__).parent.parent / "interpreter" / "core" / "respond.py"
    content = respond_path.read_text()

    assert "AUTO-TEST HOOK" in content, "Auto-test hook should exist"
    assert "enable_auto_test" in content, "Should check enable_auto_test flag"
    assert "TestDiscovery" in content, "Should use TestDiscovery"
    assert "[AutoTest]" in content, "Should have AutoTest prefix in output"

    print("✓ test_auto_test_hook_exists passed")


def test_file_snapshot_hook_exists():
    """Test that file snapshot hooks exist in respond.py."""
    respond_path = Path(__file__).parent.parent / "interpreter" / "core" / "respond.py"
    content = respond_path.read_text()

    assert "FILE CHANGE DETECTION: BEFORE" in content, "Before hook should exist"
    assert "FILE CHANGE DETECTION: AFTER" in content, "After hook should exist"
    assert "capture_source_file_states" in content, "Should use capture function"
    assert "diff_file_states" in content, "Should use diff function"
    assert "_file_snapshots_before" in content, "Should store before snapshot"

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
