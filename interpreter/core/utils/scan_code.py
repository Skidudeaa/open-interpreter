import os
import subprocess

from .temporary_file import cleanup_temporary_file, create_temporary_file

try:
    from yaspin import yaspin
    from yaspin.spinners import Spinners
except ImportError:
    pass


def scan_code(code, language, interpreter):
    """
    Scan code with semgrep
    """
    language_class = interpreter.computer.terminal.get_language(language)

    temp_file = create_temporary_file(
        code, language_class.file_extension, verbose=interpreter.verbose
    )

    temp_path = os.path.dirname(temp_file)
    file_name = os.path.basename(temp_file)

    if interpreter.verbose:
        print(f"Scanning {language} code in {file_name}")
        print("---")

    # Run semgrep
    try:
        # NOTE: Using list form instead of shell=True to avoid command injection risk.
        # The cwd parameter handles the directory change safely.
        with yaspin(text="  Scanning code...").green.right.binary as loading:
            scan = subprocess.run(
                [
                    "semgrep",
                    "scan",
                    "--config",
                    "auto",
                    "--quiet",
                    "--error",
                    file_name,
                ],
                cwd=temp_path,
                capture_output=True,
            )

        if scan.returncode == 0:
            language_name = language_class.name
            print(
                f"  {'Code Scanner: ' if interpreter.safe_mode == 'auto' else ''}No issues were found in this {language_name} code."
            )
            print("")

        # TODO(enhancement): Parse scan.stdout/stderr to extract vulnerabilities
        # and add them to the conversation history for LLM context

    except Exception as e:
        print(f"Could not scan {language} code. Have you installed 'semgrep'?")
        print(e)
        print("")  # <- Aesthetic choice

    cleanup_temporary_file(temp_file, verbose=interpreter.verbose)
