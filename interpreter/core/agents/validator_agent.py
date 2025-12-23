"""
ValidatorAgent - Code validation and testing agent.

Validates code changes through syntax checking, test execution,
and requirement verification.

Capabilities:
- Syntax validation for multiple languages
- Test discovery and execution
- Change verification
- Requirement validation
"""

import ast
import os
import re
import subprocess
from dataclasses import dataclass, field

from .base_agent import AgentRole, BaseAgent, create_result
from .types import AgentResult


@dataclass
class ValidationResult:
    """Result of a validation check."""

    check_type: str  # 'syntax', 'test', 'lint', 'requirement'
    passed: bool
    message: str
    file_path: str | None = None
    details: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Generate a summary line."""
        status = "✓" if self.passed else "✗"
        loc = f" ({self.file_path})" if self.file_path else ""
        return f"{status} [{self.check_type}]{loc}: {self.message}"


@dataclass
class TestResult:
    """Result of a test execution."""

    test_name: str
    passed: bool
    duration_ms: float = 0.0
    error_message: str | None = None
    output: str = ""


class ValidatorAgent(BaseAgent):
    """
    Agent for validating code changes and running tests.

    Performs syntax checks, discovers and runs tests, and
    validates that changes meet requirements.
    """

    role = AgentRole.VALIDATOR

    def __init__(
        self,
        interpreter,
        memory=None,
        root_path: str | None = None,
        plugins=None,
        name: str | None = None,
        timeout: int = 60,
    ):
        super().__init__(interpreter, memory, plugins=plugins, name=name)
        self.root_path = root_path or os.getcwd()
        self.timeout = timeout  # Test execution timeout in seconds

        # Track validation results
        self._validation_results: list[ValidationResult] = []
        self._test_results: list[TestResult] = []

    def get_system_message(self) -> str:
        return """You are a Validator Agent specialized in code validation and testing.

Your job is to:
1. Validate syntax of code changes
2. Discover and run relevant tests
3. Verify changes meet requirements
4. Report issues clearly

When validating:
- Check syntax before anything else
- Run related tests, not the full suite
- Be specific about what failed and why
- Suggest fixes when possible

When reviewing changes:
- Verify the change accomplishes the stated goal
- Check for edge cases
- Look for potential regressions
- Consider backwards compatibility

Always provide clear, actionable feedback."""

    def execute(self, task: str, context: str | None = None) -> "AgentResult":
        """
        Execute a validation task.

        Args:
            task: The validation task description
            context: Context with proposed changes

        Returns:
            AgentResult with validation results
        """
        self.log(f"Starting validation: {task[:50]}...")

        task_lower = task.lower()

        try:
            if "syntax" in task_lower or "check" in task_lower:
                return self._validate_syntax(task, context)

            elif "test" in task_lower or "run" in task_lower:
                return self._run_tests(task, context)

            elif "verify" in task_lower or "requirement" in task_lower:
                return self._verify_requirements(task, context)

            elif "review" in task_lower or "change" in task_lower:
                return self._review_changes(task, context)

            else:
                # General validation - combine checks
                return self._full_validation(task, context)

        except Exception as e:
            return create_result(
                role=self.role,
                success=False,
                content=f"Validation error: {str(e)}",
                error=str(e),
            )

    def validate_python_syntax(self, file_path: str) -> ValidationResult:
        """
        Validate Python file syntax.

        Args:
            file_path: Path to Python file

        Returns:
            ValidationResult
        """
        full_path = os.path.join(self.root_path, file_path)

        if not os.path.exists(full_path):
            return ValidationResult(
                check_type="syntax",
                passed=False,
                message=f"File not found: {file_path}",
                file_path=file_path,
            )

        try:
            with open(full_path, encoding="utf-8") as f:
                content = f.read()

            ast.parse(content)

            return ValidationResult(
                check_type="syntax",
                passed=True,
                message="Syntax is valid",
                file_path=file_path,
            )

        except SyntaxError as e:
            return ValidationResult(
                check_type="syntax",
                passed=False,
                message=f"Syntax error at line {e.lineno}: {e.msg}",
                file_path=file_path,
                details=[str(e)],
            )
        except Exception as e:
            return ValidationResult(
                check_type="syntax",
                passed=False,
                message=f"Error reading file: {str(e)}",
                file_path=file_path,
            )

    def validate_code_string(
        self, code: str, language: str = "python"
    ) -> ValidationResult:
        """
        Validate code string syntax.

        Args:
            code: Code to validate
            language: Programming language

        Returns:
            ValidationResult
        """
        if language == "python":
            try:
                ast.parse(code)
                return ValidationResult(
                    check_type="syntax",
                    passed=True,
                    message="Python syntax is valid",
                )
            except SyntaxError as e:
                return ValidationResult(
                    check_type="syntax",
                    passed=False,
                    message=f"Syntax error at line {e.lineno}: {e.msg}",
                    details=[str(e)],
                )
        elif language in ("javascript", "typescript", "js", "ts"):
            # Basic JS validation using node if available
            return self._validate_js_syntax(code)
        else:
            return ValidationResult(
                check_type="syntax",
                passed=True,
                message=f"No syntax validator for {language}",
            )

    def discover_tests(self, file_path: str | None = None) -> list[str]:
        """
        Discover test files related to a source file or in the project.

        Args:
            file_path: Optional source file to find related tests for

        Returns:
            List of test file paths
        """
        test_files = []

        # Common test file patterns
        test_patterns = ["test_*.py", "*_test.py", "test*.py", "*.test.js", "*.spec.js"]

        if file_path:
            # Find tests specifically for this file
            base_name = os.path.basename(file_path)
            name_without_ext = os.path.splitext(base_name)[0]

            # Look for test_<name>.py or <name>_test.py
            specific_patterns = [
                f"test_{name_without_ext}.py",
                f"{name_without_ext}_test.py",
                f"test_{name_without_ext}*.py",
            ]

            for root, _, files in os.walk(self.root_path):
                if any(
                    ignore in root for ignore in ["__pycache__", ".git", "node_modules"]
                ):
                    continue

                for f in files:
                    for pattern in specific_patterns:
                        if re.match(pattern.replace("*", ".*"), f):
                            rel_path = os.path.relpath(
                                os.path.join(root, f), self.root_path
                            )
                            test_files.append(rel_path)

        else:
            # Find all test files
            for root, dirs, files in os.walk(self.root_path):
                # Skip ignored directories
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in ["__pycache__", ".git", "node_modules", ".venv"]
                ]

                for f in files:
                    for pattern in test_patterns:
                        regex = pattern.replace("*", ".*").replace(".", "\\.")
                        if re.match(regex, f):
                            rel_path = os.path.relpath(
                                os.path.join(root, f), self.root_path
                            )
                            test_files.append(rel_path)
                            break

        return list(set(test_files))

    def run_pytest(
        self,
        test_path: str | None = None,
        specific_test: str | None = None,
    ) -> list[TestResult]:
        """
        Run pytest and return results.

        Args:
            test_path: Path to test file or directory
            specific_test: Specific test function to run

        Returns:
            List of TestResult objects
        """
        cmd = ["python", "-m", "pytest", "-v", "--tb=short"]

        if test_path:
            if specific_test:
                cmd.append(f"{test_path}::{specific_test}")
            else:
                cmd.append(test_path)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.root_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            return self._parse_pytest_output(result.stdout + result.stderr)

        except subprocess.TimeoutExpired:
            return [
                TestResult(
                    test_name="pytest",
                    passed=False,
                    error_message=f"Test execution timed out after {self.timeout}s",
                )
            ]
        except Exception as e:
            return [
                TestResult(
                    test_name="pytest",
                    passed=False,
                    error_message=str(e),
                )
            ]

    def _validate_syntax(self, task: str, context: str | None) -> "AgentResult":
        """Validate syntax of files mentioned in context."""
        files_to_check = []

        # Extract file paths from context
        if context:
            file_matches = re.findall(r"[\w/\\.-]+\.py\b", context)
            files_to_check.extend(file_matches)

        # Also look for inline code blocks
        code_blocks = re.findall(r"```python\n(.*?)```", context or "", re.DOTALL)

        results = []

        # Validate files
        for fp in set(files_to_check):
            result = self.validate_python_syntax(fp)
            results.append(result)
            self._validation_results.append(result)

        # Validate code blocks
        for i, code in enumerate(code_blocks):
            result = self.validate_code_string(code, "python")
            result.file_path = f"<code block {i + 1}>"
            results.append(result)
            self._validation_results.append(result)

        # Format output
        passed = all(r.passed for r in results)
        content_parts = [
            "## Syntax Validation Results",
            "",
            f"Checked {len(results)} items: {'All passed ✓' if passed else 'Some failed ✗'}",
            "",
        ]

        for r in results:
            content_parts.append(r.summary())
            if r.details:
                for detail in r.details[:3]:
                    content_parts.append(f"    {detail}")

        return create_result(
            role=self.role,
            success=passed,
            content="\n".join(content_parts),
            files_found=[r.file_path for r in results if r.file_path],
            tests_run=[
                {"type": "syntax", "passed": r.passed, "file": r.file_path}
                for r in results
            ],
            context_for_next="\n".join(content_parts),
        )

    def _run_tests(self, task: str, context: str | None) -> "AgentResult":
        """Discover and run tests."""
        # Find relevant source files from context
        source_files = []
        if context:
            file_matches = re.findall(r"[\w/\\.-]+\.py\b", context)
            source_files.extend(f for f in file_matches if "test" not in f.lower())

        # Discover tests
        if source_files:
            test_files = []
            for sf in source_files:
                test_files.extend(self.discover_tests(sf))
        else:
            test_files = self.discover_tests()

        test_files = list(set(test_files))[:10]  # Limit to 10 test files

        if not test_files:
            return create_result(
                role=self.role,
                success=True,
                content="No test files found",
                metadata={"tests_run": 0},
            )

        # Run tests
        all_results = []
        for tf in test_files:
            results = self.run_pytest(tf)
            all_results.extend(results)
            self._test_results.extend(results)

        passed_count = sum(1 for r in all_results if r.passed)
        failed_count = len(all_results) - passed_count

        content_parts = [
            "## Test Results",
            "",
            f"Ran {len(all_results)} tests: {passed_count} passed, {failed_count} failed",
            "",
        ]

        for r in all_results:
            status = "✓" if r.passed else "✗"
            content_parts.append(f"{status} {r.test_name}")
            if r.error_message:
                content_parts.append(f"    Error: {r.error_message[:200]}")

        return create_result(
            role=self.role,
            success=failed_count == 0,
            content="\n".join(content_parts),
            files_found=test_files,
            tests_run=[
                {"name": r.test_name, "passed": r.passed, "error": r.error_message}
                for r in all_results
            ],
            context_for_next="\n".join(content_parts),
            metadata={"passed": passed_count, "failed": failed_count},
        )

    def _verify_requirements(self, task: str, context: str | None) -> "AgentResult":
        """Verify changes meet stated requirements."""
        # Use LLM to verify requirements
        verify_prompt = f"""Review the following changes and verify they meet the stated requirements.

Requirements/Task:
{task}

Changes/Context:
{context or 'No changes provided'}

Please verify:
1. Do the changes accomplish the stated goal?
2. Are there any missing pieces?
3. Are there any potential issues or edge cases?
4. Is the implementation appropriate?

Provide a clear pass/fail verdict with reasoning."""

        messages = self.prepare_messages(verify_prompt)
        response = self.run_interpreter(messages)

        # Determine pass/fail from response
        response_lower = response.lower()
        passed = ("pass" in response_lower and "fail" not in response_lower) or (
            "accomplish" in response_lower
            and "not" not in response_lower[: response_lower.find("accomplish")]
        )

        return create_result(
            role=self.role,
            success=passed,
            content=response,
            context_for_next=response,
            metadata={"verification_type": "requirements"},
        )

    def _review_changes(self, task: str, context: str | None) -> "AgentResult":
        """Review proposed code changes."""
        review_prompt = f"""Review the following code changes for:
1. Correctness - Does it do what it's supposed to?
2. Style - Does it follow project conventions?
3. Safety - Any security or reliability concerns?
4. Completeness - Is anything missing?

Task:
{task}

Changes:
{context or 'No changes provided'}

Provide specific feedback with line references where applicable."""

        messages = self.prepare_messages(review_prompt)
        response = self.run_interpreter(messages)

        return create_result(
            role=self.role,
            success=True,
            content=response,
            context_for_next=response,
            metadata={"review_type": "code_changes"},
        )

    def _full_validation(self, task: str, context: str | None) -> "AgentResult":
        """Perform full validation: syntax + tests + requirements."""
        results = []

        # 1. Syntax validation
        syntax_result = self._validate_syntax(task, context)
        results.append(("Syntax", syntax_result))

        # 2. Run related tests (if syntax passed)
        if syntax_result.success:
            test_result = self._run_tests(task, context)
            results.append(("Tests", test_result))

        # 3. Requirements verification
        req_result = self._verify_requirements(task, context)
        results.append(("Requirements", req_result))

        # Combine results
        all_passed = all(r.success for _, r in results)

        content_parts = [
            "## Full Validation Results",
            "",
            f"Overall: {'PASSED ✓' if all_passed else 'FAILED ✗'}",
            "",
        ]

        for name, result in results:
            status = "✓" if result.success else "✗"
            content_parts.append(f"### {name} {status}")
            content_parts.append(
                result.content
                if isinstance(result.content, str)
                else str(result.content)
            )
            content_parts.append("")

        return create_result(
            role=self.role,
            success=all_passed,
            content="\n".join(content_parts),
            context_for_next="\n".join(content_parts),
            metadata={"validation_type": "full"},
        )

    def _validate_js_syntax(self, code: str) -> ValidationResult:
        """Validate JavaScript syntax using node."""
        try:
            result = subprocess.run(
                ["node", "-c", "-"],
                input=code,
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                return ValidationResult(
                    check_type="syntax",
                    passed=True,
                    message="JavaScript syntax is valid",
                )
            else:
                return ValidationResult(
                    check_type="syntax",
                    passed=False,
                    message=result.stderr.strip() or "JavaScript syntax error",
                )

        except FileNotFoundError:
            return ValidationResult(
                check_type="syntax",
                passed=True,
                message="Node.js not available for JS validation",
            )
        except Exception as e:
            return ValidationResult(
                check_type="syntax",
                passed=False,
                message=str(e),
            )

    def _parse_pytest_output(self, output: str) -> list[TestResult]:
        """Parse pytest output into TestResult objects."""
        results = []

        # Look for test results in pytest output
        # Pattern: test_file.py::test_name PASSED/FAILED
        test_pattern = r"([\w/\\.-]+::[\w_]+)\s+(PASSED|FAILED|ERROR|SKIPPED)"
        matches = re.findall(test_pattern, output)

        for test_name, status in matches:
            results.append(
                TestResult(
                    test_name=test_name,
                    passed=status == "PASSED",
                    error_message=None
                    if status == "PASSED"
                    else f"Test {status.lower()}",
                )
            )

        # If no individual results found, check overall
        if not results:
            if "passed" in output.lower():
                # Extract pass count
                match = re.search(r"(\d+) passed", output)
                count = int(match.group(1)) if match else 1
                for i in range(count):
                    results.append(TestResult(test_name=f"test_{i+1}", passed=True))
            elif "failed" in output.lower() or "error" in output.lower():
                results.append(
                    TestResult(
                        test_name="pytest",
                        passed=False,
                        error_message=output[:500],
                    )
                )
            elif "no tests" in output.lower():
                pass  # No tests to report

        return results

    def get_validation_summary(self) -> str:
        """Get summary of all validation results."""
        syntax_results = [
            r for r in self._validation_results if r.check_type == "syntax"
        ]
        test_count = len(self._test_results)
        passed_tests = sum(1 for r in self._test_results if r.passed)

        parts = ["## Validation Summary", ""]

        if syntax_results:
            passed = sum(1 for r in syntax_results if r.passed)
            parts.append(f"Syntax checks: {passed}/{len(syntax_results)} passed")

        if test_count:
            parts.append(f"Tests: {passed_tests}/{test_count} passed")

        return "\n".join(parts)
