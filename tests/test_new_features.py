"""
Tests for the 3 new advanced features:
1. File edit detection
2. Auto-test after edits
3. Trace feedback to LLM
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestFileSnapshot:
    """Test file_snapshot.py functionality."""

    def test_capture_source_file_states(self):
        """Test capturing file states."""
        from interpreter.core.utils.file_snapshot import capture_source_file_states

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test files
            test_py = Path(tmpdir) / "test.py"
            test_py.write_text("print('hello')")

            test_js = Path(tmpdir) / "test.js"
            test_js.write_text("console.log('hello')")

            # Capture states
            states = capture_source_file_states(tmpdir)

            assert len(states) == 2, f"Expected 2 files, got {len(states)}"
            assert str(test_py) in states, "test.py should be captured"
            assert str(test_js) in states, "test.js should be captured"

            # Check state structure
            for path, (mtime, content_hash, content) in states.items():
                assert isinstance(mtime, float), "mtime should be float"
                assert len(content_hash) == 32, "hash should be md5 (32 chars)"
                assert isinstance(content, str), "content should be string"

        print("✓ test_capture_source_file_states passed")

    def test_diff_file_states_modified(self):
        """Test detecting modified files."""
        from interpreter.core.utils.file_snapshot import (
            capture_source_file_states,
            diff_file_states,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            test_py = Path(tmpdir) / "test.py"
            test_py.write_text("# version 1")

            # Capture before
            before = capture_source_file_states(tmpdir)

            # Modify file
            test_py.write_text("# version 2")

            # Capture after
            after = capture_source_file_states(tmpdir)

            # Diff
            changed = diff_file_states(before, after)

            assert len(changed) == 1, f"Expected 1 changed file, got {len(changed)}"
            assert str(test_py) in changed, "test.py should be in changed"

            old_content, new_content = changed[str(test_py)]
            assert old_content == "# version 1", f"Wrong old content: {old_content}"
            assert new_content == "# version 2", f"Wrong new content: {new_content}"

        print("✓ test_diff_file_states_modified passed")

    def test_diff_file_states_new_file(self):
        """Test detecting new files."""
        from interpreter.core.utils.file_snapshot import (
            capture_source_file_states,
            diff_file_states,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # Capture before (empty)
            before = capture_source_file_states(tmpdir)
            assert len(before) == 0, "Should start empty"

            # Create new file
            new_file = Path(tmpdir) / "new.py"
            new_file.write_text("# new file")

            # Capture after
            after = capture_source_file_states(tmpdir)

            # Diff
            changed = diff_file_states(before, after)

            assert len(changed) == 1, f"Expected 1 new file, got {len(changed)}"
            old_content, new_content = changed[str(new_file)]
            assert old_content == "", "Old content should be empty for new file"
            assert new_content == "# new file", f"Wrong new content: {new_content}"

        print("✓ test_diff_file_states_new_file passed")

    def test_diff_file_states_deleted(self):
        """Test detecting deleted files."""
        from interpreter.core.utils.file_snapshot import (
            capture_source_file_states,
            diff_file_states,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            test_py = Path(tmpdir) / "test.py"
            test_py.write_text("# will be deleted")

            # Capture before
            before = capture_source_file_states(tmpdir)

            # Delete file
            test_py.unlink()

            # Capture after
            after = capture_source_file_states(tmpdir)

            # Diff
            changed = diff_file_states(before, after)

            assert len(changed) == 1, f"Expected 1 deleted file, got {len(changed)}"
            old_content, new_content = changed[str(test_py)]
            assert old_content == "# will be deleted", f"Wrong old content"
            assert new_content == "", "New content should be empty for deleted file"

        print("✓ test_diff_file_states_deleted passed")

    def test_skip_directories(self):
        """Test that venv, node_modules, etc. are skipped."""
        from interpreter.core.utils.file_snapshot import capture_source_file_states

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files in skip directories
            venv_dir = Path(tmpdir) / "venv"
            venv_dir.mkdir()
            (venv_dir / "skip.py").write_text("# should skip")

            node_dir = Path(tmpdir) / "node_modules"
            node_dir.mkdir()
            (node_dir / "skip.js").write_text("// should skip")

            # Create file that should be captured
            (Path(tmpdir) / "capture.py").write_text("# should capture")

            states = capture_source_file_states(tmpdir)

            assert len(states) == 1, f"Expected 1 file, got {len(states)}"
            assert any("capture.py" in p for p in states), "capture.py should be captured"
            assert not any("skip" in p for p in states), "skip files should not be captured"

        print("✓ test_skip_directories passed")


class TestCoreFlags:
    """Test that new flags are properly defined."""

    def test_flags_exist(self):
        """Test that enable_auto_test and enable_trace_feedback exist."""
        from interpreter import interpreter

        assert hasattr(interpreter, "enable_auto_test"), "enable_auto_test should exist"
        assert hasattr(interpreter, "enable_trace_feedback"), "enable_trace_feedback should exist"

        # Should be False by default (unless OI_ACTIVATE_ALL is set)
        # Just check they're boolean
        assert isinstance(interpreter.enable_auto_test, bool), "enable_auto_test should be bool"
        assert isinstance(interpreter.enable_trace_feedback, bool), "enable_trace_feedback should be bool"

        print("✓ test_flags_exist passed")

    def test_activate_all_features(self):
        """Test that activate_all_features enables the new flags."""
        from interpreter.core.core import OpenInterpreter

        interp = OpenInterpreter()
        assert interp.enable_auto_test == False, "Should start disabled"
        assert interp.enable_trace_feedback == False, "Should start disabled"

        interp.activate_all_features()

        assert interp.enable_auto_test == True, "Should be enabled after activate_all"
        assert interp.enable_trace_feedback == True, "Should be enabled after activate_all"

        print("✓ test_activate_all_features passed")


class TestMemoryExports:
    """Test that memory module exports the new function."""

    def test_create_edit_from_file_change_exists(self):
        """Test that create_edit_from_file_change is exported."""
        from interpreter.core.memory import create_edit_from_file_change

        assert callable(create_edit_from_file_change), "Should be callable"
        print("✓ test_create_edit_from_file_change_exists passed")

    def test_create_edit_from_file_change_works(self):
        """Test that create_edit_from_file_change creates an Edit."""
        from interpreter.core.memory import create_edit_from_file_change, Edit

        edit = create_edit_from_file_change(
            file_path="/test/file.py",
            original_content="# old",
            new_content="# new",
            user_message="Update the file",
        )

        assert isinstance(edit, Edit), f"Should return Edit, got {type(edit)}"
        assert edit.file_path == "/test/file.py", "Wrong file_path"
        assert edit.original_content == "# old", "Wrong original_content"
        assert edit.new_content == "# new", "Wrong new_content"

        print("✓ test_create_edit_from_file_change_works passed")


class TestStatusBar:
    """Test that status bar shows new features."""

    def test_features_banner_includes_new_features(self):
        """Test that FeaturesBanner shows auto-test and trace-fb."""
        from interpreter.terminal_interface.components.status_bar import FeaturesBanner
        from interpreter.core.core import OpenInterpreter

        interp = OpenInterpreter()
        interp.activate_all_features()

        banner = FeaturesBanner(interp)
        features = banner.get_enabled_features()

        assert "auto-test" in features, "auto-test should be in features"
        assert "trace-fb" in features, "trace-fb should be in features"

        print("✓ test_features_banner_includes_new_features passed")


def run_all_tests():
    """Run all test classes."""
    print("=" * 60)
    print("Testing New Features")
    print("=" * 60)

    test_classes = [
        TestFileSnapshot(),
        TestCoreFlags(),
        TestMemoryExports(),
        TestStatusBar(),
    ]

    total = 0
    passed = 0

    for test_class in test_classes:
        class_name = test_class.__class__.__name__
        print(f"\n--- {class_name} ---")

        for method_name in dir(test_class):
            if method_name.startswith("test_"):
                total += 1
                try:
                    getattr(test_class, method_name)()
                    passed += 1
                except Exception as e:
                    print(f"✗ {method_name} FAILED: {e}")

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{total} tests passed")
    print("=" * 60)

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
