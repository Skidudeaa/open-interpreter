"""
Semgrep code scanning utility.

# ARCHITECTURE: Integrates Semgrep static analysis into code execution flow.
# WHY: Provides security scanning before code runs, surfacing vulnerabilities to users/LLM.
# TRADEOFF: JSON parsing adds complexity vs simple return code check, but enables actionable feedback.
"""

import json
import logging
import os
import subprocess
from typing import Any

from .temporary_file import cleanup_temporary_file, create_temporary_file

# Module logger for scan-level debugging
logger = logging.getLogger(__name__)

try:
    from yaspin import yaspin
except ImportError:
    yaspin = None


def parse_semgrep_output(stdout: str, stderr: str) -> list[dict[str, Any]]:
    """
    Parse Semgrep JSON output into structured vulnerability list.

    Args:
        stdout: JSON output from Semgrep
        stderr: Error output from Semgrep

    Returns:
        List of vulnerability dictionaries with rule_id, message, severity, line, code_snippet, file
    """
    vulnerabilities: list[dict[str, Any]] = []

    try:
        if not stdout.strip():
            return vulnerabilities

        data = json.loads(stdout)
        results = data.get("results", [])

        for result in results:
            vuln = {
                "rule_id": result.get("check_id", "unknown"),
                "message": result.get("extra", {}).get("message", "No description"),
                "severity": result.get("extra", {}).get("severity", "WARNING"),
                "line": result.get("start", {}).get("line", 0),
                "code_snippet": result.get("extra", {}).get("lines", ""),
                "file": result.get("path", ""),
            }
            vulnerabilities.append(vuln)

    except json.JSONDecodeError as e:
        logger.debug(f"Failed to parse Semgrep JSON output: {e}")
        # Fall back to stderr if JSON parsing fails
        if stderr.strip():
            vulnerabilities.append(
                {
                    "rule_id": "parse_error",
                    "message": stderr.strip()[:200],
                    "severity": "ERROR",
                    "line": 0,
                    "code_snippet": "",
                    "file": "",
                }
            )

    return vulnerabilities


def scan_code(code, language, interpreter) -> list[dict[str, Any]]:
    """
    Scan code with Semgrep for security vulnerabilities.

    Args:
        code: The code to scan
        language: Programming language of the code
        interpreter: OpenInterpreter instance for config access

    Returns:
        List of vulnerability dictionaries (empty if no issues found)
    """
    language_class = interpreter.computer.terminal.get_language(language)

    temp_file = create_temporary_file(
        code, language_class.file_extension, verbose=interpreter.verbose
    )

    temp_path = os.path.dirname(temp_file)
    file_name = os.path.basename(temp_file)
    language_name = language_class.name

    if interpreter.verbose:
        print(f"Scanning {language} code in {file_name}")
        print("---")

    vulnerabilities: list[dict[str, Any]] = []

    # Run semgrep with JSON output for parsing
    try:
        # NOTE: Using list form instead of shell=True to avoid command injection risk.
        # The cwd parameter handles the directory change safely.
        with yaspin(text="  Scanning code...").green.right.binary:
            scan = subprocess.run(
                [
                    "semgrep",
                    "scan",
                    "--config",
                    "auto",
                    "--json",  # JSON output for structured parsing
                    file_name,
                ],
                cwd=temp_path,
                capture_output=True,
                text=True,  # Get string output instead of bytes
            )

        if scan.returncode == 0:
            print(
                f"  {'Code Scanner: ' if interpreter.safe_mode == 'auto' else ''}No issues were found in this {language_name} code."
            )
            print("")
            return []

        # Parse Semgrep JSON output
        vulnerabilities = parse_semgrep_output(scan.stdout, scan.stderr)

        if vulnerabilities:
            print(f"\n  ⚠️  Found {len(vulnerabilities)} issue(s):\n")
            for vuln in vulnerabilities:
                severity = vuln["severity"]
                message = vuln["message"]
                line = vuln["line"]
                code_snippet = vuln["code_snippet"]

                print(f"    [{severity}] {message}")
                if line > 0:
                    snippet_preview = code_snippet[:60] if code_snippet else ""
                    print(f"        Line {line}: {snippet_preview}")
                print()
        else:
            # Semgrep returned non-zero but no vulnerabilities parsed
            # This could be a config/network issue
            logger.debug(
                f"Semgrep returned {scan.returncode} but no vulnerabilities parsed"
            )
            if scan.stderr.strip():
                logger.debug(f"Semgrep stderr: {scan.stderr[:500]}")

    except FileNotFoundError:
        print(f"Could not scan {language} code. Have you installed 'semgrep'?")
        print("  Install with: pip install semgrep")
        print("")
    except Exception as e:
        print(f"Could not scan {language} code. Have you installed 'semgrep'?")
        logger.debug(f"Semgrep scan failed: {e}")
        print("")

    cleanup_temporary_file(temp_file, verbose=interpreter.verbose)
    return vulnerabilities
