"""
AutoCommit - Automatic git commits for successful code edits.

# ARCHITECTURE: Hooks into respond.py after edit recording to create semantic commits.
# WHY: Provides automatic version control like aider-ce without manual intervention.
# TRADEOFF: Adds git operations after each edit vs. granular commit history.
# NOTE: Non-blocking - git failures are logged but don't interrupt execution.

Provides semantic commit messages based on Edit metadata:
- edit_type: bug_fix, feature, refactor, etc.
- primary_symbol: The main code symbol affected
- user_intent: Natural language description from conversation
"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..memory.edit_record import Edit, EditType

logger = logging.getLogger(__name__)


@dataclass
class CommitResult:
    """Result of an auto-commit operation."""

    success: bool
    commit_hash: str | None = None
    message: str | None = None
    error: str | None = None


class AutoCommitter:
    """
    Automatic git committer for code edits.

    Creates semantic commit messages based on Edit metadata and
    integrates with the Semantic Edit Graph for commit hash tracking.

    Usage:
        committer = AutoCommitter(project_root="/path/to/project")
        result = committer.commit_edits(edits)
        if result.success:
            print(f"Committed: {result.commit_hash}")
    """

    def __init__(self, project_root: str | None = None):
        """
        Initialize the auto-committer.

        Args:
            project_root: Root directory of the project
        """
        self.project_root = project_root or "."
        self._is_git = self._check_git_repo()

    def _check_git_repo(self) -> bool:
        """Check if we're in a git repository."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    @property
    def is_git_repo(self) -> bool:
        """Check if we're in a git repository."""
        return self._is_git

    def is_file_tracked(self, file_path: str) -> bool:
        """Check if a file is tracked by git."""
        try:
            result = subprocess.run(
                ["git", "ls-files", file_path],
                cwd=self.project_root,
                capture_output=True,
                text=True,
            )
            return bool(result.stdout.strip())
        except Exception:
            return False

    def commit_edit(self, edit: Edit) -> CommitResult:
        """
        Commit a single edit.

        Args:
            edit: The Edit object to commit

        Returns:
            CommitResult with success status and commit hash
        """
        return self.commit_edits([edit])

    def commit_edits(self, edits: list[Edit]) -> CommitResult:
        """
        Commit multiple edits as a single commit.

        # WHY: Batch commits preserve logical units of work from single LLM responses.
        # TRADEOFF: Less granular history vs. meaningful atomic commits.

        Args:
            edits: List of Edit objects to commit

        Returns:
            CommitResult with success status and commit hash
        """
        if not self._is_git:
            return CommitResult(success=False, error="Not a git repository")

        if not edits:
            return CommitResult(success=False, error="No edits to commit")

        # Filter to existing files only
        trackable_edits = []
        for edit in edits:
            if edit.file_path and Path(self.project_root, edit.file_path).exists():
                trackable_edits.append(edit)
            elif edit.file_path and Path(edit.file_path).exists():
                trackable_edits.append(edit)

        if not trackable_edits:
            return CommitResult(success=False, error="No trackable files to commit")

        # Stage files
        files_to_stage = [e.file_path for e in trackable_edits]
        if not self._stage_files(files_to_stage):
            return CommitResult(success=False, error="Failed to stage files")

        # Check if there are staged changes
        if not self._has_staged_changes():
            return CommitResult(success=False, error="No changes to commit")

        # Generate commit message
        message = self._generate_commit_message(trackable_edits)

        # Create commit
        commit_hash = self._create_commit(message)

        if commit_hash:
            return CommitResult(success=True, commit_hash=commit_hash, message=message)
        else:
            return CommitResult(success=False, error="Commit creation failed")

    def _stage_files(self, file_paths: list[str]) -> bool:
        """Stage files for commit."""
        try:
            result = subprocess.run(
                ["git", "add"] + file_paths,
                cwd=self.project_root,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except Exception as e:
            logger.debug(f"Failed to stage files: {e}")
            return False

    def _has_staged_changes(self) -> bool:
        """Check if there are staged changes."""
        try:
            result = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=self.project_root,
                capture_output=True,
            )
            # Returns 1 if there are differences (changes staged)
            return result.returncode == 1
        except Exception:
            return False

    def _create_commit(self, message: str) -> str | None:
        """Create a git commit and return the hash."""
        try:
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.project_root,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                logger.debug(f"Commit failed: {result.stderr}")
                return None

            # Get the commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
            )

            if hash_result.returncode == 0:
                return hash_result.stdout.strip()

            return None

        except Exception as e:
            logger.debug(f"Commit failed: {e}")
            return None

    def _generate_commit_message(self, edits: list[Edit]) -> str:
        """
        Generate a semantic commit message from edits.

        Format:
            [OI] {edit_type}: {primary_symbol or filename}

            {user_intent}

            Files: {file_list}
            Affected: {symbol_list}
        """
        # Determine primary edit type (most common among edits)
        edit_types = [e.edit_type for e in edits if e.edit_type != EditType.UNKNOWN]
        if edit_types:
            primary_type = max(set(edit_types), key=edit_types.count)
        else:
            primary_type = EditType.UNKNOWN

        # Get primary symbol or filename
        primary_symbol = None
        for edit in edits:
            if edit.primary_symbol:
                primary_symbol = edit.primary_symbol.name
                break

        if not primary_symbol:
            # Use first filename as fallback
            first_file = edits[0].file_path
            if first_file:
                primary_symbol = Path(first_file).stem
            else:
                primary_symbol = "code"

        # Get user intent
        user_intent = None
        for edit in edits:
            if edit.user_intent:
                user_intent = edit.user_intent
                break
            if edit.conversation_context and edit.conversation_context.intent_summary:
                user_intent = edit.conversation_context.intent_summary
                break

        if not user_intent:
            user_intent = f"Auto-committed {len(edits)} file(s)"

        # Truncate intent to 72 chars for commit subject line
        if len(user_intent) > 72:
            user_intent = user_intent[:69] + "..."

        # Build file list
        file_list = ", ".join(Path(e.file_path).name for e in edits if e.file_path)

        # Build symbol list
        symbols = []
        for edit in edits:
            if edit.primary_symbol:
                symbols.append(edit.primary_symbol.name)
            symbols.extend(s.name for s in edit.affected_symbols[:3])
        symbols = list(dict.fromkeys(symbols))[:5]  # Unique, max 5
        symbol_list = ", ".join(symbols) if symbols else "N/A"

        # Format type name
        type_name = primary_type.value.replace("_", " ")

        # Build message
        subject = f"[OI] {type_name}: {primary_symbol}"
        body = f"\n\n{user_intent}\n\nFiles: {file_list}\nAffected: {symbol_list}"

        return subject + body


# Convenience functions for respond.py


def auto_commit_edit(
    edit: Edit,
    project_root: str | None = None,
) -> str | None:
    """
    Auto-commit a single edit.

    Args:
        edit: The Edit to commit
        project_root: Project root directory

    Returns:
        Commit hash if successful, None otherwise
    """
    committer = AutoCommitter(project_root)
    result = committer.commit_edit(edit)

    if result.success:
        logger.info(f"Auto-committed: {result.commit_hash[:8]}")
        return result.commit_hash
    else:
        logger.debug(f"Auto-commit skipped: {result.error}")
        return None


def batch_auto_commit(
    edits: list[Edit],
    project_root: str | None = None,
) -> str | None:
    """
    Auto-commit multiple edits as a single commit.

    Args:
        edits: List of Edits to commit
        project_root: Project root directory

    Returns:
        Commit hash if successful, None otherwise
    """
    committer = AutoCommitter(project_root)
    result = committer.commit_edits(edits)

    if result.success:
        logger.info(f"Auto-committed {len(edits)} edit(s): {result.commit_hash[:8]}")
        return result.commit_hash
    else:
        logger.debug(f"Auto-commit skipped: {result.error}")
        return None
