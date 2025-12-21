"""
EditValidator - Main validation orchestrator.

Validates code edits using:
1. Syntax checking
2. Optional type checking (mypy)
3. Related test discovery and execution
4. Rollback on failure

No Docker required - uses temp files and subprocess isolation.
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any

from .syntax_checker import SyntaxChecker, SyntaxCheckResult
from .test_discovery import TestDiscovery, TestRunResult
from .rollback import EditRollback


@dataclass
class ValidationResult:
    """Complete result of edit validation."""
    valid: bool
    syntax_result: Optional[SyntaxCheckResult] = None
    type_check_result: Optional[Dict[str, Any]] = None
    test_result: Optional[TestRunResult] = None

    # Summary fields
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_context_string(self) -> str:
        """Convert to string for LLM context."""
        parts = [f"## Validation Result: {'PASSED' if self.valid else 'FAILED'}"]

        if self.syntax_result:
            parts.append(f"\n### Syntax: {'OK' if self.syntax_result.valid else 'FAILED'}")
            if not self.syntax_result.valid:
                for error in self.syntax_result.errors:
                    parts.append(f"- {error}")

        if self.type_check_result:
            type_ok = self.type_check_result.get("passed", False)
            parts.append(f"\n### Type Check: {'OK' if type_ok else 'FAILED'}")
            if not type_ok:
                for error in self.type_check_result.get("errors", [])[:5]:
                    parts.append(f"- {error}")

        if self.test_result:
            parts.append(f"\n### Tests: {'PASSED' if self.test_result.passed else 'FAILED'}")
            parts.append(f"- {self.test_result.passed_tests}/{self.test_result.total_tests} passed")
            if self.test_result.failed_test_names:
                parts.append("- Failed tests:")
                for name in self.test_result.failed_test_names[:5]:
                    parts.append(f"  - {name}")

        if self.errors:
            parts.append("\n### Errors")
            for error in self.errors:
                parts.append(f"- {error}")

        return "\n".join(parts)


class EditValidator:
    """
    Validates code edits before applying them.

    Usage:
        validator = EditValidator(project_root="/path/to/project")
        result = validator.validate_edit(
            file_path="src/module.py",
            original_content=original,
            new_content=modified,
        )
        if result.valid:
            # Apply the edit
        else:
            print(result.to_context_string())
    """

    def __init__(
        self,
        project_root: Optional[str] = None,
        run_tests: bool = True,
        run_type_check: bool = True,
        test_timeout: int = 300,
    ):
        """
        Initialize the validator.

        Args:
            project_root: Root directory of the project
            run_tests: Run related tests as part of validation
            run_type_check: Run type checking (if mypy available)
            test_timeout: Timeout for test runs in seconds
        """
        self.project_root = project_root or os.getcwd()
        self.run_tests = run_tests
        self.run_type_check = run_type_check
        self.test_timeout = test_timeout

        # Initialize components
        self.syntax_checker = SyntaxChecker()
        self.test_discovery = TestDiscovery(project_root=self.project_root)
        self.rollback = EditRollback(project_root=self.project_root)

        # Check for type checker
        self._mypy_available = shutil.which('mypy') is not None

    def validate_edit(
        self,
        file_path: str,
        original_content: str,
        new_content: str,
    ) -> ValidationResult:
        """
        Validate a proposed edit.

        Args:
            file_path: Path to the file being edited
            original_content: Original file content
            new_content: Proposed new content

        Returns:
            ValidationResult with all validation details
        """
        result = ValidationResult(valid=True)

        # Step 1: Syntax check
        syntax_result = self.syntax_checker.check(new_content, file_path)
        result.syntax_result = syntax_result

        if not syntax_result.valid:
            result.valid = False
            result.errors.extend([str(e) for e in syntax_result.errors])
            return result  # Don't proceed with broken syntax

        result.warnings.extend(syntax_result.warnings)

        # Step 2: Type check (if enabled and available)
        if self.run_type_check and self._mypy_available:
            type_result = self._run_type_check(file_path, new_content)
            result.type_check_result = type_result

            if not type_result.get("passed", True):
                result.warnings.append("Type check failed (non-blocking)")
                result.warnings.extend(type_result.get("errors", [])[:3])

        # Step 3: Apply edit temporarily and run tests
        if self.run_tests:
            test_result = self._validate_with_tests(
                file_path, original_content, new_content
            )
            result.test_result = test_result

            if not test_result.passed:
                result.valid = False
                result.errors.append(f"Tests failed: {test_result.failed_tests} failures")

        return result

    def validate_syntax_only(
        self,
        file_path: str,
        content: str,
    ) -> SyntaxCheckResult:
        """
        Quick syntax-only validation.

        Args:
            file_path: Path to the file
            content: Content to validate

        Returns:
            SyntaxCheckResult
        """
        return self.syntax_checker.check(content, file_path)

    def _run_type_check(
        self,
        file_path: str,
        content: str,
    ) -> Dict[str, Any]:
        """Run mypy type check on the content."""
        if not file_path.endswith('.py'):
            return {"passed": True, "skipped": True}

        # Write to temp file
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.py',
            delete=False
        ) as f:
            f.write(content)
            temp_path = f.name

        try:
            result = subprocess.run(
                ['mypy', '--no-error-summary', '--no-color-output', temp_path],
                capture_output=True,
                text=True,
                timeout=60,
            )

            errors = []
            for line in result.stdout.split('\n'):
                if 'error:' in line:
                    # Clean up temp path in error messages
                    clean_line = line.replace(temp_path, file_path)
                    errors.append(clean_line)

            return {
                "passed": result.returncode == 0,
                "errors": errors,
            }

        except subprocess.TimeoutExpired:
            return {"passed": True, "warning": "Type check timed out"}

        except Exception as e:
            return {"passed": True, "warning": str(e)}

        finally:
            Path(temp_path).unlink(missing_ok=True)

    def _validate_with_tests(
        self,
        file_path: str,
        original_content: str,
        new_content: str,
    ) -> TestRunResult:
        """
        Apply edit temporarily and run related tests.

        Args:
            file_path: Path to the file
            original_content: Original content
            new_content: New content

        Returns:
            TestRunResult
        """
        full_path = Path(self.project_root) / file_path

        # Backup original
        self.rollback.backup_file(file_path)

        try:
            # Apply new content
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(new_content)

            # Find and run related tests
            related_tests = self.test_discovery.find_related_tests(file_path)

            if not related_tests:
                # No related tests found
                return TestRunResult(
                    passed=True,
                    total_tests=0,
                    passed_tests=0,
                    failed_tests=0,
                    skipped_tests=0,
                    duration_seconds=0,
                    output="No related tests found",
                )

            # Run the tests
            result = self.test_discovery.run_tests(
                related_tests,
                timeout_seconds=self.test_timeout,
            )

            return result

        finally:
            # Always restore original
            self.rollback.restore_file(file_path)

    def create_sandbox_validator(self) -> "SandboxValidator":
        """
        Create a sandbox validator for isolated testing.

        The sandbox uses a temporary directory with copies of
        relevant files, allowing edits without affecting the
        main project.
        """
        return SandboxValidator(self)


class SandboxValidator:
    """
    Validates edits in an isolated sandbox environment.

    Creates a temporary copy of the project (or relevant parts)
    and validates edits there without affecting the main project.
    """

    def __init__(
        self,
        parent_validator: EditValidator,
        copy_full_project: bool = False,
    ):
        """
        Initialize the sandbox validator.

        Args:
            parent_validator: Parent EditValidator
            copy_full_project: Copy full project (slow) or just relevant files
        """
        self.parent = parent_validator
        self.copy_full_project = copy_full_project
        self._sandbox_dir: Optional[str] = None

    def __enter__(self):
        self._create_sandbox()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._cleanup_sandbox()
        return False

    def validate_edit(
        self,
        file_path: str,
        new_content: str,
    ) -> ValidationResult:
        """
        Validate an edit in the sandbox.

        Args:
            file_path: Path to the file
            new_content: Proposed content

        Returns:
            ValidationResult
        """
        if not self._sandbox_dir:
            self._create_sandbox()

        # Write new content to sandbox
        sandbox_path = Path(self._sandbox_dir) / file_path
        sandbox_path.parent.mkdir(parents=True, exist_ok=True)

        with open(sandbox_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        # Create a validator for the sandbox
        sandbox_validator = EditValidator(
            project_root=self._sandbox_dir,
            run_tests=self.parent.run_tests,
            run_type_check=self.parent.run_type_check,
            test_timeout=self.parent.test_timeout,
        )

        # Read original for comparison
        original_path = Path(self.parent.project_root) / file_path
        if original_path.exists():
            original = original_path.read_text()
        else:
            original = ""

        return sandbox_validator.validate_edit(file_path, original, new_content)

    def _create_sandbox(self):
        """Create the sandbox directory."""
        self._sandbox_dir = tempfile.mkdtemp(prefix="edit_sandbox_")

        if self.copy_full_project:
            # Copy entire project (excluding large/unnecessary files)
            shutil.copytree(
                self.parent.project_root,
                self._sandbox_dir,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    '__pycache__', '*.pyc', '.git', 'node_modules',
                    '.venv', 'venv', '*.egg-info', 'dist', 'build',
                ),
            )

    def _cleanup_sandbox(self):
        """Clean up the sandbox directory."""
        if self._sandbox_dir and Path(self._sandbox_dir).exists():
            shutil.rmtree(self._sandbox_dir, ignore_errors=True)
            self._sandbox_dir = None


# Convenience function
def validate_edit(
    file_path: str,
    original_content: str,
    new_content: str,
    project_root: Optional[str] = None,
) -> ValidationResult:
    """
    Validate a code edit.

    Args:
        file_path: Path to the file
        original_content: Original content
        new_content: New content
        project_root: Project root directory

    Returns:
        ValidationResult
    """
    validator = EditValidator(project_root=project_root)
    return validator.validate_edit(file_path, original_content, new_content)
