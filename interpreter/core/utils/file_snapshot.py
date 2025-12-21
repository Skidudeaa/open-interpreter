"""
File state capture and diffing for edit detection.

Captures file states before/after code execution to detect
arbitrary file modifications made by executed code.
"""
import hashlib
from pathlib import Path
from typing import Dict, Tuple, Set

# Source file extensions to track
SOURCE_EXTENSIONS: Set[str] = {
    '.py', '.js', '.ts', '.jsx', '.tsx',   # Code
    '.json', '.yaml', '.yml', '.toml',      # Config
    '.md', '.rst', '.txt',                  # Docs
    '.html', '.css', '.scss',               # Web
    '.sql', '.sh', '.bash',                 # Scripts
}

# Directories to skip
SKIP_DIRS: Set[str] = {
    'venv', 'env', '.venv', 'node_modules', '__pycache__',
    '.git', '.svn', '.hg', 'dist', 'build', '.tox', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', 'eggs', '.eggs', '*.egg-info',
}


def capture_source_file_states(
    root_dir: str,
    max_files: int = 500
) -> Dict[str, Tuple[float, str, str]]:
    """
    Capture mtime, hash, and content of source files.

    Args:
        root_dir: Directory to scan for source files
        max_files: Maximum number of files to capture (prevents slowdown)

    Returns:
        Dict mapping file_path to (mtime, content_hash, content)
    """
    states = {}
    root = Path(root_dir).resolve()

    try:
        for src_file in root.rglob("*"):
            if len(states) >= max_files:
                break
            if not src_file.is_file():
                continue
            if src_file.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            # Skip non-source directories
            if any(part in SKIP_DIRS or part.startswith('.') for part in src_file.parts):
                continue
            try:
                stat = src_file.stat()
                content = src_file.read_text(errors='ignore')
                content_hash = hashlib.md5(content.encode()).hexdigest()
                states[str(src_file)] = (stat.st_mtime, content_hash, content)
            except (OSError, IOError, UnicodeDecodeError):
                continue
    except Exception:
        pass  # Non-blocking

    return states


def diff_file_states(
    before: Dict[str, Tuple[float, str, str]],
    after: Dict[str, Tuple[float, str, str]]
) -> Dict[str, Tuple[str, str]]:
    """
    Compare before/after states, return changed files.

    Args:
        before: File states before execution
        after: File states after execution

    Returns:
        Dict mapping file_path to (original_content, new_content)
    """
    changed = {}

    for file_path, (mtime_after, hash_after, content_after) in after.items():
        if file_path in before:
            mtime_before, hash_before, content_before = before[file_path]
            # Check if content actually changed (hash comparison is fast)
            if hash_before != hash_after:
                changed[file_path] = (content_before, content_after)
        else:
            # New file created
            changed[file_path] = ("", content_after)

    # Check for deleted files
    for file_path in before:
        if file_path not in after:
            content_before = before[file_path][2]
            changed[file_path] = (content_before, "")  # Empty string = deleted

    return changed
