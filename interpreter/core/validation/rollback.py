"""
EditRollback - Git-based rollback mechanism for failed edits.

Provides safe rollback of code changes using:
- File backups (in-memory and on disk)
- Git stash/unstash for version-controlled files
- Git worktrees for isolated testing (optional)

No Docker required - uses git and filesystem operations.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class FileBackup:
    """Backup of a file's content."""
    file_path: str
    original_content: str
    backup_path: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    git_tracked: bool = False


@dataclass
class RollbackResult:
    """Result of a rollback operation."""
    success: bool
    files_restored: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class EditRollback:
    """
    Manages rollback of code edits.

    Provides multiple rollback strategies:
    1. In-memory backups (fastest, lost on process exit)
    2. Disk backups (persistent, uses .bak files)
    3. Git stash (for version-controlled files)

    Usage:
        rollback = EditRollback(project_root="/path/to/project")
        rollback.backup_file("src/module.py")
        # ... make edits ...
        if validation_failed:
            rollback.restore_all()
    """

    def __init__(
        self,
        project_root: Optional[str] = None,
        use_git: bool = True,
        backup_dir: Optional[str] = None,
    ):
        """
        Initialize the rollback manager.

        Args:
            project_root: Root directory of the project
            use_git: Use git operations when available
            backup_dir: Directory for disk backups (default: .edit_backups)
        """
        self.project_root = project_root or os.getcwd()
        self.use_git = use_git and self._is_git_repo()
        self.backup_dir = backup_dir or os.path.join(self.project_root, ".edit_backups")

        # In-memory backups
        self._backups: Dict[str, FileBackup] = {}

        # Track stashed changes
        self._stash_created = False

    def backup_file(self, file_path: str, use_disk: bool = False) -> bool:
        """
        Create a backup of a file before editing.

        Args:
            file_path: Path to the file (relative to project root)
            use_disk: Also create a disk backup

        Returns:
            True if backup was created successfully
        """
        full_path = Path(self.project_root) / file_path

        if not full_path.exists():
            return False

        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()

            backup = FileBackup(
                file_path=file_path,
                original_content=content,
                git_tracked=self._is_git_tracked(file_path),
            )

            # Create disk backup if requested
            if use_disk:
                backup.backup_path = self._create_disk_backup(file_path, content)

            self._backups[file_path] = backup
            return True

        except Exception:
            return False

    def restore_file(self, file_path: str) -> bool:
        """
        Restore a file from backup.

        Args:
            file_path: Path to the file

        Returns:
            True if restored successfully
        """
        if file_path not in self._backups:
            return False

        backup = self._backups[file_path]
        full_path = Path(self.project_root) / file_path

        try:
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(backup.original_content)

            # Clean up disk backup if it exists
            if backup.backup_path and Path(backup.backup_path).exists():
                Path(backup.backup_path).unlink()

            del self._backups[file_path]
            return True

        except Exception:
            return False

    def restore_all(self) -> RollbackResult:
        """
        Restore all backed up files.

        Returns:
            RollbackResult with details
        """
        result = RollbackResult(success=True)

        for file_path in list(self._backups.keys()):
            if self.restore_file(file_path):
                result.files_restored.append(file_path)
            else:
                result.errors.append(f"Failed to restore {file_path}")
                result.success = False

        # Pop stash if we created one
        if self._stash_created and self.use_git:
            self._git_stash_pop()
            self._stash_created = False

        return result

    def discard_backups(self):
        """Discard all backups (edits were successful)."""
        # Clean up disk backups
        for backup in self._backups.values():
            if backup.backup_path and Path(backup.backup_path).exists():
                Path(backup.backup_path).unlink()

        self._backups.clear()

        # Drop stash if we created one
        if self._stash_created and self.use_git:
            self._git_stash_drop()
            self._stash_created = False

    def stash_changes(self, message: str = "Edit validation stash") -> bool:
        """
        Stash current changes using git.

        This creates a save point before applying edits, allowing
        easy rollback even if in-memory backups are lost.

        Args:
            message: Stash message

        Returns:
            True if stash was created
        """
        if not self.use_git:
            return False

        try:
            result = subprocess.run(
                ["git", "stash", "push", "-m", message],
                cwd=self.project_root,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0 and "No local changes" not in result.stdout:
                self._stash_created = True
                return True

            return False

        except Exception:
            return False

    def get_backup(self, file_path: str) -> Optional[FileBackup]:
        """Get backup info for a file."""
        return self._backups.get(file_path)

    def has_backup(self, file_path: str) -> bool:
        """Check if a file has a backup."""
        return file_path in self._backups

    def get_all_backups(self) -> List[FileBackup]:
        """Get all current backups."""
        return list(self._backups.values())

    def _create_disk_backup(self, file_path: str, content: str) -> str:
        """Create a backup file on disk."""
        # Ensure backup directory exists
        Path(self.backup_dir).mkdir(parents=True, exist_ok=True)

        # Create backup filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = file_path.replace("/", "_").replace("\\", "_")
        backup_name = f"{safe_name}.{timestamp}.bak"
        backup_path = os.path.join(self.backup_dir, backup_name)

        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return backup_path

    def _is_git_repo(self) -> bool:
        """Check if project root is a git repository."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.project_root,
                capture_output=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _is_git_tracked(self, file_path: str) -> bool:
        """Check if a file is tracked by git."""
        try:
            result = subprocess.run(
                ["git", "ls-files", "--error-unmatch", file_path],
                cwd=self.project_root,
                capture_output=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _git_stash_pop(self) -> bool:
        """Pop the last stash."""
        try:
            result = subprocess.run(
                ["git", "stash", "pop"],
                cwd=self.project_root,
                capture_output=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _git_stash_drop(self) -> bool:
        """Drop the last stash."""
        try:
            result = subprocess.run(
                ["git", "stash", "drop"],
                cwd=self.project_root,
                capture_output=True,
            )
            return result.returncode == 0
        except Exception:
            return False

    def cleanup_old_backups(self, max_age_hours: int = 24):
        """Clean up old disk backups."""
        if not Path(self.backup_dir).exists():
            return

        cutoff = datetime.now().timestamp() - (max_age_hours * 3600)

        for backup_file in Path(self.backup_dir).glob("*.bak"):
            if backup_file.stat().st_mtime < cutoff:
                backup_file.unlink()


class TransactionalEdit:
    """
    Context manager for transactional edits.

    Usage:
        with TransactionalEdit(project_root) as tx:
            tx.backup("file1.py")
            tx.backup("file2.py")
            # ... make edits ...
            if validation_failed:
                tx.rollback()
            else:
                tx.commit()
    """

    def __init__(self, project_root: Optional[str] = None):
        self.rollback_mgr = EditRollback(project_root)
        self._committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None or not self._committed:
            self.rollback_mgr.restore_all()
        else:
            self.rollback_mgr.discard_backups()
        return False

    def backup(self, file_path: str) -> bool:
        """Backup a file before editing."""
        return self.rollback_mgr.backup_file(file_path)

    def rollback(self) -> RollbackResult:
        """Rollback all changes."""
        return self.rollback_mgr.restore_all()

    def commit(self):
        """Mark the transaction as committed."""
        self._committed = True
        self.rollback_mgr.discard_backups()
