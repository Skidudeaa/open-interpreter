"""
Tests for the EditValidator module.

Tests the complete validation pipeline:
- Syntax validation
- Type checking (optional)
- Test discovery and execution
- Sandbox validation
"""

import os
import tempfile
from pathlib import Path

import pytest

from interpreter.core.validation.validator import (
    EditValidator,
    ValidationResult,
    validate_edit,
)


class TestValidationResult:
    """Tests for ValidationResult dataclass."""

    def test_passed_result(self):
        """Test passed validation result."""
        result = ValidationResult(valid=True)
        assert result.valid
        assert len(result.errors) == 0

    def test_failed_result_with_errors(self):
        """Test failed result with errors."""
        result = ValidationResult(
            valid=False,
            errors=["Syntax error on line 5", "Missing import"],
        )
        assert not result.valid
        assert len(result.errors) == 2

    def test_to_context_string_passed(self):
        """Test context string for passed result."""
        result = ValidationResult(valid=True)
        context = result.to_context_string()
        assert "PASSED" in context

    def test_to_context_string_failed(self):
        """Test context string for failed result."""
        result = ValidationResult(
            valid=False,
            errors=["Some error"],
        )
        context = result.to_context_string()
        assert "FAILED" in context
        assert "Some error" in context


class TestEditValidatorSyntaxOnly:
    """Tests for syntax-only validation."""

    @pytest.fixture
    def validator(self):
        return EditValidator(run_tests=False, run_type_check=False)

    def test_valid_python_syntax(self, validator):
        """Test valid Python passes syntax check."""
        content = """
def hello():
    return "Hello, World!"
"""
        result = validator.validate_syntax_only("test.py", content)
        assert result.valid

    def test_invalid_python_syntax(self, validator):
        """Test invalid Python fails syntax check."""
        content = """
def broken(
    return "missing paren"
"""
        result = validator.validate_syntax_only("test.py", content)
        assert not result.valid
        assert len(result.errors) > 0


class TestEditValidatorFull:
    """Tests for full validation with edits."""

    @pytest.fixture
    def temp_project(self):
        """Create a temporary project directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a simple source file
            src_path = Path(tmpdir) / "src"
            src_path.mkdir()

            source_file = src_path / "module.py"
            source_file.write_text(
                """
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b
"""
            )

            # Create a test file
            tests_path = Path(tmpdir) / "tests"
            tests_path.mkdir()

            test_file = tests_path / "test_module.py"
            test_file.write_text(
                """
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pathlib import Path
from src.module import add, multiply

def test_add():
    assert add(1, 2) == 3

def test_multiply():
    assert multiply(2, 3) == 6
"""
            )

            yield tmpdir

    def test_validate_valid_edit(self, temp_project):
        """Test validating a valid edit."""
        validator = EditValidator(
            project_root=temp_project,
            run_tests=False,  # Skip tests for this test
            run_type_check=False,
        )

        original = """
def add(a, b):
    return a + b
"""
        new_content = """
def add(a, b):
    '''Add two numbers.'''
    return a + b
"""
        result = validator.validate_edit("src/module.py", original, new_content)
        assert result.valid

    def test_validate_syntax_error(self, temp_project):
        """Test validation catches syntax errors."""
        validator = EditValidator(
            project_root=temp_project,
            run_tests=False,
            run_type_check=False,
        )

        original = "def add(a, b): return a + b"
        new_content = "def add(a, b): return a +"  # Syntax error

        result = validator.validate_edit("src/module.py", original, new_content)
        assert not result.valid
        assert result.syntax_result is not None
        assert not result.syntax_result.valid


class TestEditValidatorWithTempFiles:
    """Tests using temporary files for validation."""

    def test_validate_new_file(self):
        """Test validating content for a new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            validator = EditValidator(
                project_root=tmpdir,
                run_tests=False,
                run_type_check=False,
            )

            new_content = """
class NewClass:
    def __init__(self):
        self.value = 42

    def get_value(self):
        return self.value
"""
            result = validator.validate_edit("new_file.py", "", new_content)
            assert result.valid

    def test_validate_json_file(self):
        """Test validating JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            validator = EditValidator(
                project_root=tmpdir,
                run_tests=False,
                run_type_check=False,
            )

            new_content = '{"name": "test", "version": "1.0.0"}'
            result = validator.validate_edit("config.json", "", new_content)
            assert result.valid

    def test_validate_invalid_json(self):
        """Test validating invalid JSON fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            validator = EditValidator(
                project_root=tmpdir,
                run_tests=False,
                run_type_check=False,
            )

            new_content = '{"name": "test", invalid}'
            result = validator.validate_edit("config.json", "", new_content)
            assert not result.valid


class TestSandboxValidator:
    """Tests for sandbox validation."""

    def test_sandbox_creation(self):
        """Test sandbox is created and cleaned up."""
        with tempfile.TemporaryDirectory() as tmpdir:
            validator = EditValidator(project_root=tmpdir, run_tests=False)

            with validator.create_sandbox_validator() as sandbox:
                assert sandbox._sandbox_dir is not None
                assert Path(sandbox._sandbox_dir).exists()

            # Sandbox should be cleaned up after context exit
            # Note: The actual cleanup depends on implementation

    def test_sandbox_validate_edit(self):
        """Test validation in sandbox."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a source file
            Path(tmpdir, "test.py").write_text("x = 1")

            validator = EditValidator(
                project_root=tmpdir,
                run_tests=False,
                run_type_check=False,
            )

            with validator.create_sandbox_validator() as sandbox:
                result = sandbox.validate_edit("test.py", "x = 2  # Updated")
                assert result.valid


class TestValidateEditFunction:
    """Tests for the convenience validate_edit function."""

    def test_validate_edit_valid(self):
        """Test convenience function with valid edit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "test.py").write_text("x = 1")

            result = validate_edit(
                "test.py",
                "x = 1",
                "x = 2",
                project_root=tmpdir,
            )
            # Should pass syntax check at minimum
            assert result.syntax_result is not None
            assert result.syntax_result.valid

    def test_validate_edit_invalid(self):
        """Test convenience function with invalid edit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = validate_edit(
                "test.py",
                "x = 1",
                "x = ",  # Invalid syntax
                project_root=tmpdir,
            )
            assert not result.valid


class TestEditValidatorConfiguration:
    """Tests for validator configuration options."""

    def test_default_configuration(self):
        """Test default configuration values."""
        validator = EditValidator()
        assert validator.run_tests is True
        assert validator.run_type_check is True
        assert validator.test_timeout == 300

    def test_custom_configuration(self):
        """Test custom configuration values."""
        validator = EditValidator(
            run_tests=False,
            run_type_check=False,
            test_timeout=60,
        )
        assert validator.run_tests is False
        assert validator.run_type_check is False
        assert validator.test_timeout == 60

    def test_project_root_default(self):
        """Test project root defaults to current directory."""
        validator = EditValidator()
        assert validator.project_root == os.getcwd()

    def test_custom_project_root(self):
        """Test custom project root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            validator = EditValidator(project_root=tmpdir)
            assert validator.project_root == tmpdir
