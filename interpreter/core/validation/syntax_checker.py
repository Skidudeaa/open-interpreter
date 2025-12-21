"""
SyntaxChecker - Validates code syntax before applying edits.

Supports multiple languages:
- Python: Uses ast.parse()
- JavaScript/TypeScript: Uses node --check
- JSON: Uses json.loads()
- Others: Basic checks or no validation
"""

import ast
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class SyntaxErrorInfo:
    """Details about a syntax error."""
    line: int
    column: int
    message: str
    file_path: str = ""

    def __str__(self) -> str:
        location = f"{self.file_path}:" if self.file_path else ""
        return f"{location}{self.line}:{self.column}: {self.message}"


@dataclass
class SyntaxCheckResult:
    """Result of a syntax check."""
    valid: bool
    errors: List[SyntaxErrorInfo] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    language: str = ""

    def __str__(self) -> str:
        if self.valid:
            return f"Syntax OK ({self.language})"
        return f"Syntax errors:\n" + "\n".join(str(e) for e in self.errors)


class SyntaxChecker:
    """
    Multi-language syntax checker.

    Usage:
        checker = SyntaxChecker()
        result = checker.check(code, "test.py")
        if not result.valid:
            print(result.errors)
    """

    # File extension to language mapping
    LANGUAGE_MAP = {
        '.py': 'python',
        '.pyw': 'python',
        '.js': 'javascript',
        '.mjs': 'javascript',
        '.cjs': 'javascript',
        '.ts': 'typescript',
        '.tsx': 'typescript',
        '.jsx': 'javascript',
        '.json': 'json',
        '.sh': 'shell',
        '.bash': 'shell',
        '.zsh': 'shell',
    }

    def __init__(self):
        """Initialize the syntax checker."""
        # Cache available tools
        self._node_available = shutil.which('node') is not None
        self._tsc_available = shutil.which('tsc') is not None

    def check(
        self,
        code: str,
        file_path: str,
        language: Optional[str] = None
    ) -> SyntaxCheckResult:
        """
        Check syntax of code.

        Args:
            code: The source code to check
            file_path: Path to the file (used for language detection)
            language: Optional language override

        Returns:
            SyntaxCheckResult with validation details
        """
        # Determine language
        if language is None:
            ext = Path(file_path).suffix.lower()
            language = self.LANGUAGE_MAP.get(ext, 'unknown')

        # Dispatch to appropriate checker
        if language == 'python':
            return self._check_python(code, file_path)
        elif language == 'javascript':
            return self._check_javascript(code, file_path)
        elif language == 'typescript':
            return self._check_typescript(code, file_path)
        elif language == 'json':
            return self._check_json(code, file_path)
        elif language == 'shell':
            return self._check_shell(code, file_path)
        else:
            # Unknown language - can't validate
            return SyntaxCheckResult(
                valid=True,
                warnings=[f"No syntax checker available for {language}"],
                language=language
            )

    def _check_python(self, code: str, file_path: str) -> SyntaxCheckResult:
        """Check Python syntax using AST."""
        try:
            ast.parse(code)
            return SyntaxCheckResult(valid=True, language='python')
        except SyntaxError as e:
            return SyntaxCheckResult(
                valid=False,
                errors=[SyntaxErrorInfo(
                    line=e.lineno or 0,
                    column=e.offset or 0,
                    message=e.msg or str(e),
                    file_path=file_path,
                )],
                language='python'
            )
        except Exception as e:
            return SyntaxCheckResult(
                valid=False,
                errors=[SyntaxErrorInfo(
                    line=0,
                    column=0,
                    message=str(e),
                    file_path=file_path,
                )],
                language='python'
            )

    def _check_javascript(self, code: str, file_path: str) -> SyntaxCheckResult:
        """Check JavaScript syntax using Node.js."""
        if not self._node_available:
            return SyntaxCheckResult(
                valid=True,
                warnings=["Node.js not available for JS syntax checking"],
                language='javascript'
            )

        # Write to temp file and check with node --check
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.js',
            delete=False
        ) as f:
            f.write(code)
            temp_path = f.name

        try:
            result = subprocess.run(
                ['node', '--check', temp_path],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                return SyntaxCheckResult(valid=True, language='javascript')
            else:
                # Parse error from stderr
                error_msg = result.stderr.strip()
                line, col = self._parse_node_error(error_msg)
                return SyntaxCheckResult(
                    valid=False,
                    errors=[SyntaxErrorInfo(
                        line=line,
                        column=col,
                        message=error_msg,
                        file_path=file_path,
                    )],
                    language='javascript'
                )

        except subprocess.TimeoutExpired:
            return SyntaxCheckResult(
                valid=False,
                errors=[SyntaxErrorInfo(0, 0, "Syntax check timed out", file_path)],
                language='javascript'
            )
        except Exception as e:
            return SyntaxCheckResult(
                valid=True,
                warnings=[f"Error running node: {e}"],
                language='javascript'
            )
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def _check_typescript(self, code: str, file_path: str) -> SyntaxCheckResult:
        """Check TypeScript syntax."""
        # For now, just check if it parses as JavaScript
        # Full TypeScript checking would require tsc
        if not self._tsc_available:
            # Fall back to basic JS check
            return self._check_javascript(code, file_path)

        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.ts',
            delete=False
        ) as f:
            f.write(code)
            temp_path = f.name

        try:
            result = subprocess.run(
                ['tsc', '--noEmit', '--skipLibCheck', temp_path],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                return SyntaxCheckResult(valid=True, language='typescript')
            else:
                return SyntaxCheckResult(
                    valid=False,
                    errors=[SyntaxErrorInfo(0, 0, result.stderr.strip()[:500], file_path)],
                    language='typescript'
                )

        except Exception as e:
            return SyntaxCheckResult(
                valid=True,
                warnings=[f"Error running tsc: {e}"],
                language='typescript'
            )
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def _check_json(self, code: str, file_path: str) -> SyntaxCheckResult:
        """Check JSON syntax."""
        try:
            json.loads(code)
            return SyntaxCheckResult(valid=True, language='json')
        except json.JSONDecodeError as e:
            return SyntaxCheckResult(
                valid=False,
                errors=[SyntaxErrorInfo(
                    line=e.lineno,
                    column=e.colno,
                    message=e.msg,
                    file_path=file_path,
                )],
                language='json'
            )

    def _check_shell(self, code: str, file_path: str) -> SyntaxCheckResult:
        """Check shell script syntax using bash -n."""
        if not shutil.which('bash'):
            return SyntaxCheckResult(
                valid=True,
                warnings=["bash not available for shell syntax checking"],
                language='shell'
            )

        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.sh',
            delete=False
        ) as f:
            f.write(code)
            temp_path = f.name

        try:
            result = subprocess.run(
                ['bash', '-n', temp_path],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                return SyntaxCheckResult(valid=True, language='shell')
            else:
                return SyntaxCheckResult(
                    valid=False,
                    errors=[SyntaxErrorInfo(0, 0, result.stderr.strip(), file_path)],
                    language='shell'
                )

        except Exception as e:
            return SyntaxCheckResult(
                valid=True,
                warnings=[f"Error running bash: {e}"],
                language='shell'
            )
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def _parse_node_error(self, error_msg: str) -> Tuple[int, int]:
        """Parse line and column from Node.js error message."""
        import re

        # Try to find line:column pattern
        match = re.search(r':(\d+):(\d+)', error_msg)
        if match:
            return int(match.group(1)), int(match.group(2))

        # Try just line number
        match = re.search(r':(\d+)', error_msg)
        if match:
            return int(match.group(1)), 0

        return 0, 0


# Convenience function
def check_syntax(code: str, file_path: str) -> SyntaxCheckResult:
    """
    Check syntax of code.

    Args:
        code: Source code to check
        file_path: Path to the file

    Returns:
        SyntaxCheckResult
    """
    checker = SyntaxChecker()
    return checker.check(code, file_path)
