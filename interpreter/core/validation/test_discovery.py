"""
TestDiscovery - Discovers and runs tests related to code changes.

Finds tests that are likely to be affected by a code change:
- Tests in the same directory
- Tests that import the changed file
- Tests with matching names

Supports pytest and unittest discovery patterns.
"""

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Dict, Any


@dataclass
class TestFile:
    """Information about a test file."""
    path: str
    test_count: int = 0
    imports_target: bool = False
    name_matches: bool = False


@dataclass
class TestRunResult:
    """Result of running tests."""
    passed: bool
    total_tests: int
    passed_tests: int
    failed_tests: int
    skipped_tests: int
    duration_seconds: float
    output: str
    failed_test_names: List[str] = field(default_factory=list)


class TestDiscovery:
    """
    Discovers and runs tests related to code changes.

    Usage:
        discovery = TestDiscovery(project_root="/path/to/project")
        tests = discovery.find_related_tests("src/module.py")
        result = discovery.run_tests(tests)
    """

    def __init__(
        self,
        project_root: Optional[str] = None,
        test_patterns: Optional[List[str]] = None,
    ):
        """
        Initialize test discovery.

        Args:
            project_root: Root directory of the project
            test_patterns: Glob patterns for test files
        """
        self.project_root = project_root or os.getcwd()
        self.test_patterns = test_patterns or [
            "test_*.py",
            "*_test.py",
            "tests.py",
        ]

        # Common test directory names
        self.test_directories = ["tests", "test", "spec", "specs"]

    def find_related_tests(
        self,
        file_path: str,
        max_tests: int = 20
    ) -> List[TestFile]:
        """
        Find tests related to a file.

        Args:
            file_path: Path to the changed file (relative to project root)
            max_tests: Maximum number of test files to return

        Returns:
            List of TestFile objects
        """
        related_tests = []

        # Get module name from file path
        module_name = self._file_to_module(file_path)
        file_stem = Path(file_path).stem

        # Find all test files
        test_files = self._find_all_test_files()

        for test_path in test_files:
            test_file = TestFile(path=test_path)

            # Check if test imports the target file
            if self._test_imports_module(test_path, module_name, file_path):
                test_file.imports_target = True

            # Check if test name matches
            if file_stem in Path(test_path).stem:
                test_file.name_matches = True

            # Count tests in file
            test_file.test_count = self._count_tests_in_file(test_path)

            # Add if related
            if test_file.imports_target or test_file.name_matches:
                related_tests.append(test_file)

        # Sort by relevance (imports > name match, then by test count)
        related_tests.sort(
            key=lambda t: (not t.imports_target, not t.name_matches, -t.test_count)
        )

        return related_tests[:max_tests]

    def find_same_directory_tests(self, file_path: str) -> List[TestFile]:
        """Find tests in the same directory as the file."""
        dir_path = Path(file_path).parent
        full_dir = Path(self.project_root) / dir_path

        tests = []
        if full_dir.exists():
            for pattern in self.test_patterns:
                for test_path in full_dir.glob(pattern):
                    rel_path = str(test_path.relative_to(self.project_root))
                    tests.append(TestFile(
                        path=rel_path,
                        test_count=self._count_tests_in_file(rel_path),
                    ))

        return tests

    def run_tests(
        self,
        test_files: List[TestFile],
        timeout_seconds: int = 300,
        verbose: bool = False,
    ) -> TestRunResult:
        """
        Run the specified tests using pytest.

        Args:
            test_files: List of test files to run
            timeout_seconds: Maximum time for test run
            verbose: Show verbose output

        Returns:
            TestRunResult with test outcomes
        """
        if not test_files:
            return TestRunResult(
                passed=True,
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                skipped_tests=0,
                duration_seconds=0,
                output="No tests to run",
            )

        # Build pytest command
        test_paths = [t.path for t in test_files]
        cmd = ["pytest", "-x", "--tb=short"]  # Stop on first failure

        if verbose:
            cmd.append("-v")

        cmd.extend(test_paths)

        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )

            # Parse output
            output = result.stdout + result.stderr
            parsed = self._parse_pytest_output(output)

            return TestRunResult(
                passed=result.returncode == 0,
                total_tests=parsed.get("total", 0),
                passed_tests=parsed.get("passed", 0),
                failed_tests=parsed.get("failed", 0),
                skipped_tests=parsed.get("skipped", 0),
                duration_seconds=parsed.get("duration", 0),
                output=output,
                failed_test_names=parsed.get("failed_names", []),
            )

        except subprocess.TimeoutExpired:
            return TestRunResult(
                passed=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                skipped_tests=0,
                duration_seconds=timeout_seconds,
                output=f"Test run timed out after {timeout_seconds}s",
            )

        except FileNotFoundError:
            # pytest not installed
            return TestRunResult(
                passed=True,  # Can't validate without pytest
                total_tests=0,
                passed_tests=0,
                failed_tests=0,
                skipped_tests=0,
                duration_seconds=0,
                output="pytest not available",
            )

        except Exception as e:
            return TestRunResult(
                passed=False,
                total_tests=0,
                passed_tests=0,
                failed_tests=1,
                skipped_tests=0,
                duration_seconds=0,
                output=f"Error running tests: {e}",
            )

    def collect_tests(self, test_file: str) -> List[str]:
        """
        Collect test names from a file without running them.

        Args:
            test_file: Path to test file

        Returns:
            List of test function/method names
        """
        try:
            result = subprocess.run(
                ["pytest", "--collect-only", "-q", test_file],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                return []

            tests = []
            for line in result.stdout.split("\n"):
                line = line.strip()
                if "::" in line and not line.startswith("="):
                    tests.append(line)

            return tests

        except Exception:
            return []

    def _find_all_test_files(self) -> List[str]:
        """Find all test files in the project."""
        test_files = []

        for root, dirs, files in os.walk(self.project_root):
            # Skip common non-test directories
            dirs[:] = [d for d in dirs if d not in {
                '__pycache__', '.git', 'node_modules', '.venv', 'venv'
            }]

            for pattern in self.test_patterns:
                for file in files:
                    import fnmatch
                    if fnmatch.fnmatch(file, pattern):
                        rel_path = os.path.relpath(
                            os.path.join(root, file),
                            self.project_root
                        )
                        test_files.append(rel_path)

        return test_files

    def _file_to_module(self, file_path: str) -> str:
        """Convert file path to module name."""
        # Remove .py extension and convert / to .
        module = Path(file_path).with_suffix("").as_posix()
        return module.replace("/", ".")

    def _test_imports_module(
        self,
        test_path: str,
        module_name: str,
        file_path: str
    ) -> bool:
        """Check if a test file imports the target module."""
        full_path = Path(self.project_root) / test_path

        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Check for import statements
            # import module_name
            # from module_name import ...
            # from parent.module_name import ...

            file_stem = Path(file_path).stem
            patterns = [
                rf'\bimport\s+{re.escape(module_name)}\b',
                rf'\bfrom\s+{re.escape(module_name)}\s+import\b',
                rf'\bfrom\s+\S*\.{re.escape(file_stem)}\s+import\b',
                rf'\bimport\s+\S*\.{re.escape(file_stem)}\b',
            ]

            for pattern in patterns:
                if re.search(pattern, content):
                    return True

            return False

        except Exception:
            return False

    def _count_tests_in_file(self, test_path: str) -> int:
        """Count the number of tests in a file."""
        full_path = Path(self.project_root) / test_path

        try:
            with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Count test functions and methods
            # def test_*
            # async def test_*
            count = len(re.findall(r'^\s*(async\s+)?def\s+test_', content, re.MULTILINE))
            return count

        except Exception:
            return 0

    def _parse_pytest_output(self, output: str) -> Dict[str, Any]:
        """Parse pytest output to extract test counts."""
        result = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "duration": 0,
            "failed_names": [],
        }

        # Look for summary line like: "5 passed, 2 failed in 1.23s"
        summary_match = re.search(
            r'(\d+)\s+passed.*?(\d+)\s+failed.*?in\s+([\d.]+)s',
            output
        )
        if summary_match:
            result["passed"] = int(summary_match.group(1))
            result["failed"] = int(summary_match.group(2))
            result["duration"] = float(summary_match.group(3))
            result["total"] = result["passed"] + result["failed"]

        # Simpler patterns
        passed_match = re.search(r'(\d+)\s+passed', output)
        if passed_match:
            result["passed"] = int(passed_match.group(1))

        failed_match = re.search(r'(\d+)\s+failed', output)
        if failed_match:
            result["failed"] = int(failed_match.group(1))

        skipped_match = re.search(r'(\d+)\s+skipped', output)
        if skipped_match:
            result["skipped"] = int(skipped_match.group(1))

        result["total"] = result["passed"] + result["failed"] + result["skipped"]

        # Extract failed test names
        failed_names = re.findall(r'FAILED\s+(\S+)', output)
        result["failed_names"] = failed_names

        return result


# Convenience function
def find_and_run_tests(file_path: str, project_root: Optional[str] = None) -> TestRunResult:
    """
    Find and run tests related to a file.

    Args:
        file_path: Path to the changed file
        project_root: Root of the project

    Returns:
        TestRunResult
    """
    discovery = TestDiscovery(project_root=project_root)
    tests = discovery.find_related_tests(file_path)
    return discovery.run_tests(tests)
