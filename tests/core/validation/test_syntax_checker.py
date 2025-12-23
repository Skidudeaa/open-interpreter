"""
Tests for the SyntaxChecker module.

Tests syntax validation for multiple languages:
- Python (using ast)
- JavaScript (using node if available)
- JSON
- Shell scripts
"""

import pytest

from interpreter.core.validation.syntax_checker import (
    SyntaxChecker,
    SyntaxCheckResult,
    SyntaxErrorInfo,
    check_syntax,
)


class TestSyntaxErrorInfo:
    """Tests for SyntaxErrorInfo dataclass."""

    def test_str_representation_with_file(self):
        """Test string representation includes file path."""
        error = SyntaxErrorInfo(
            line=10,
            column=5,
            message="unexpected indent",
            file_path="test.py",
        )
        assert str(error) == "test.py:10:5: unexpected indent"

    def test_str_representation_without_file(self):
        """Test string representation without file path."""
        error = SyntaxErrorInfo(
            line=10,
            column=5,
            message="unexpected indent",
        )
        assert str(error) == "10:5: unexpected indent"


class TestSyntaxCheckResult:
    """Tests for SyntaxCheckResult dataclass."""

    def test_valid_result_str(self):
        """Test string representation of valid result."""
        result = SyntaxCheckResult(valid=True, language="python")
        assert "Syntax OK" in str(result)
        assert "python" in str(result)

    def test_invalid_result_str(self):
        """Test string representation of invalid result."""
        result = SyntaxCheckResult(
            valid=False,
            errors=[SyntaxErrorInfo(1, 0, "error message")],
            language="python",
        )
        result_str = str(result)
        assert "Syntax errors" in result_str
        assert "error message" in result_str


class TestSyntaxCheckerPython:
    """Tests for Python syntax checking."""

    @pytest.fixture
    def checker(self):
        return SyntaxChecker()

    def test_valid_python(self, checker):
        """Test valid Python code passes."""
        code = """
def hello():
    print("Hello, World!")

class MyClass:
    def __init__(self):
        self.value = 42
"""
        result = checker.check(code, "test.py")
        assert result.valid
        assert result.language == "python"
        assert len(result.errors) == 0

    def test_valid_python_with_async(self, checker):
        """Test valid async Python code."""
        code = """
async def async_function():
    await some_coroutine()
    return 42
"""
        result = checker.check(code, "test.py")
        assert result.valid

    def test_invalid_python_syntax(self, checker):
        """Test invalid Python code fails."""
        code = """
def broken(
    print("missing closing paren"
"""
        result = checker.check(code, "test.py")
        assert not result.valid
        assert len(result.errors) > 0
        assert result.language == "python"

    def test_invalid_python_indentation(self, checker):
        """Test indentation error is caught."""
        code = """
def hello():
print("bad indent")
"""
        result = checker.check(code, "test.py")
        assert not result.valid

    def test_empty_python(self, checker):
        """Test empty Python code is valid."""
        result = checker.check("", "test.py")
        assert result.valid

    def test_python_with_comments_only(self, checker):
        """Test Python file with only comments is valid."""
        code = """
# This is a comment
# Another comment
'''
Docstring
'''
"""
        result = checker.check(code, "test.py")
        assert result.valid


class TestSyntaxCheckerJSON:
    """Tests for JSON syntax checking."""

    @pytest.fixture
    def checker(self):
        return SyntaxChecker()

    def test_valid_json(self, checker):
        """Test valid JSON passes."""
        code = '{"name": "test", "value": 42, "nested": {"a": 1}}'
        result = checker.check(code, "test.json")
        assert result.valid
        assert result.language == "json"

    def test_valid_json_array(self, checker):
        """Test valid JSON array."""
        code = '[1, 2, 3, {"key": "value"}]'
        result = checker.check(code, "test.json")
        assert result.valid

    def test_invalid_json(self, checker):
        """Test invalid JSON fails."""
        code = '{"name": "test", "value": }'
        result = checker.check(code, "test.json")
        assert not result.valid
        assert len(result.errors) > 0

    def test_invalid_json_trailing_comma(self, checker):
        """Test JSON with trailing comma fails."""
        code = '{"name": "test",}'
        result = checker.check(code, "test.json")
        assert not result.valid


class TestSyntaxCheckerJavaScript:
    """Tests for JavaScript syntax checking."""

    @pytest.fixture
    def checker(self):
        return SyntaxChecker()

    def test_valid_javascript(self, checker):
        """Test valid JavaScript passes (if node available)."""
        code = """
function hello() {
    console.log("Hello");
}

const arrow = (x) => x * 2;
"""
        result = checker.check(code, "test.js")
        # Either valid or warning about node not available
        assert result.valid or result.warnings

    def test_language_detection(self, checker):
        """Test language is correctly detected from extension."""
        result = checker.check("const x = 1;", "test.js")
        assert result.language == "javascript"

        result = checker.check("const x = 1;", "test.mjs")
        assert result.language == "javascript"


class TestSyntaxCheckerLanguageDetection:
    """Tests for language detection."""

    @pytest.fixture
    def checker(self):
        return SyntaxChecker()

    def test_python_extensions(self, checker):
        """Test Python file extensions are detected."""
        for ext in [".py", ".pyw"]:
            result = checker.check("x = 1", f"test{ext}")
            assert result.language == "python"

    def test_javascript_extensions(self, checker):
        """Test JavaScript file extensions are detected."""
        for ext in [".js", ".mjs", ".cjs", ".jsx"]:
            result = checker.check("const x = 1;", f"test{ext}")
            assert result.language == "javascript"

    def test_typescript_extensions(self, checker):
        """Test TypeScript file extensions are detected."""
        for ext in [".ts", ".tsx"]:
            result = checker.check("const x: number = 1;", f"test{ext}")
            # TypeScript falls back to JavaScript if tsc not available
            assert result.language in ("typescript", "javascript")

    def test_shell_extensions(self, checker):
        """Test shell file extensions are detected."""
        for ext in [".sh", ".bash", ".zsh"]:
            result = checker.check("echo hello", f"test{ext}")
            assert result.language == "shell"

    def test_unknown_extension(self, checker):
        """Test unknown extensions return valid with warning."""
        result = checker.check("some content", "test.xyz")
        assert result.valid
        assert any("No syntax checker" in w for w in result.warnings)

    def test_language_override(self, checker):
        """Test language can be overridden."""
        result = checker.check('{"key": "value"}', "data.txt", language="json")
        assert result.language == "json"
        assert result.valid


class TestCheckSyntaxFunction:
    """Tests for the convenience check_syntax function."""

    def test_check_syntax_valid(self):
        """Test convenience function with valid code."""
        result = check_syntax("x = 1", "test.py")
        assert result.valid

    def test_check_syntax_invalid(self):
        """Test convenience function with invalid code."""
        result = check_syntax("def broken(:", "test.py")
        assert not result.valid


class TestSyntaxCheckerShell:
    """Tests for shell script syntax checking."""

    @pytest.fixture
    def checker(self):
        return SyntaxChecker()

    def test_valid_shell(self, checker):
        """Test valid shell script passes."""
        code = """#!/bin/bash
echo "Hello"
for i in 1 2 3; do
    echo $i
done
"""
        result = checker.check(code, "test.sh")
        # Either valid or warning about bash not available
        assert result.valid or result.warnings

    def test_shell_with_functions(self, checker):
        """Test shell script with functions."""
        code = """
my_function() {
    echo "In function"
}
my_function
"""
        result = checker.check(code, "test.sh")
        assert result.valid or result.warnings
