"""
SurgeonAgent - Precise code editing agent with transactional guarantees.

ARCHITECTURE: Makes minimal, correct code changes with optional
Scout collaboration. Supports atomic multi-edit transactions with
rollback on failure.

KEY INVARIANTS:
- Edits are validated before application
- Multi-edit transactions are atomic (all succeed or all rollback)
- Optimistic locking prevents blind overwrites of changed files
- Backup chain enables rollback to any previous state
"""

import ast
import difflib
import hashlib
import os
import re
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .base_agent import AgentResult, AgentRole, BaseAgent, create_result

try:
    from ...terminal_interface.components.activity_stream import emit_activity
except ImportError:

    def emit_activity(*args, **kwargs):
        pass


@dataclass
class EditProposal:
    """
    A proposed code edit with content hashing for optimistic locking.

    The content_hash captures file state at proposal time. Apply will
    fail if file has changed, preventing blind overwrites.
    """

    file_path: str
    original_content: str
    new_content: str
    description: str
    confidence: float = 0.8
    content_hash: str = field(default="")

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = self._hash_content(self.original_content)

    @staticmethod
    def _hash_content(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @property
    def diff(self) -> str:
        """Generate unified diff."""
        return "\n".join(
            difflib.unified_diff(
                self.original_content.splitlines(keepends=True),
                self.new_content.splitlines(keepends=True),
                fromfile=f"a/{self.file_path}",
                tofile=f"b/{self.file_path}",
            )
        )

    def verify_unchanged(self, current_content: str) -> bool:
        """Check file hasn't been modified since proposal."""
        return self._hash_content(current_content) == self.content_hash


@dataclass
class EditTransaction:
    """
    Atomic multi-edit transaction with rollback support.

    WHY: Partial edit application leaves codebase in broken state.
    Transaction ensures all-or-nothing semantics.
    """

    edits: list[EditProposal] = field(default_factory=list)
    backups: dict[str, str] = field(default_factory=dict)  # path -> original content
    applied: list[str] = field(default_factory=list)  # paths of applied edits

    def add(self, edit: EditProposal):
        self.edits.append(edit)

    def record_backup(self, path: str, content: str):
        if path not in self.backups:
            self.backups[path] = content

    def mark_applied(self, path: str):
        self.applied.append(path)


class SurgeonAgent(BaseAgent):
    """
    Agent for making precise code edits with transactional guarantees.

    CRITICAL BEHAVIORS:
    - Multi-edit operations are atomic
    - Optimistic locking prevents race conditions
    - Failed transactions auto-rollback
    """

    role = AgentRole.SURGEON

    # Edit block parsing pattern - handles content containing keywords
    EDIT_PATTERN = re.compile(
        r"```edit\s*\n"
        r"FILE:\s*(?P<file>.+?)\s*\n"
        r"FIND:\n(?P<find>.*?)\n"
        r"REPLACE:\n(?P<replace>.*?)"
        r"(?=\n```(?:\s*\n|$))",
        re.DOTALL,
    )

    def __init__(
        self,
        interpreter,
        memory=None,
        root_path: str | None = None,
        validate_syntax: bool = True,
        plugins=None,
        name: str | None = None,
    ):
        super().__init__(interpreter, memory, plugins=plugins, name=name)
        self.root_path = Path(root_path or os.getcwd())
        self.validate_syntax = validate_syntax

        # Edit tracking
        self._proposed_edits: list[EditProposal] = []
        self._applied_edits: list[EditProposal] = []
        self._transaction_history: list[EditTransaction] = []

    def _validate_path(self, file_path: str) -> Path | None:
        """
        Validate that file_path stays within root_path.

        Prevents path traversal attacks (e.g., '../../../etc/passwd').

        Args:
            file_path: Relative path from root_path

        Returns:
            Resolved Path if valid, None if path escapes root_path
        """
        try:
            # Construct and resolve to handle '../' sequences
            full_path = (self.root_path / file_path).resolve()
            root_resolved = self.root_path.resolve()

            # Verify path is still within root
            full_path.relative_to(root_resolved)
            return full_path
        except ValueError:
            # relative_to() raises ValueError if path is not relative to root
            self.log(f"Path traversal blocked: {file_path}")
            return None
        except Exception as e:
            self.log(f"Path validation error: {e}")
            return None

    def get_system_message(self) -> str:
        return """You are a Surgeon Agent specialized in precise code editing.

Make minimal, correct changes. Preserve style. No unsolicited improvements.

Edit format (EXACT - do not deviate):
```edit
FILE: path/to/file.py
FIND:
<exact text to find, including whitespace>
REPLACE:
<replacement text>
```

RULES:
- FIND must match file content EXACTLY (whitespace matters)
- One logical change per edit block
- Multiple edit blocks allowed
- No explanatory text inside edit blocks"""

    def execute(self, task: str, context: str | None = None) -> AgentResult:
        """Execute surgical edit with optional Scout collaboration."""
        self.log(f"Starting surgical edit: {task[:50]}...")
        emit_activity("plan", "Planning code changes", task[:40], agent="surgeon")

        # Gather context if needed
        if self._needs_more_context(task, context):
            emit_activity("search", "Gathering additional context", agent="surgeon")
            context = self._gather_additional_context(task, context)

        messages = self.prepare_messages(task, context)

        emit_activity("think", "Generating edit proposals", agent="surgeon")
        response = self.run_interpreter(messages)

        edits = self._parse_edit_proposals(response)

        if not edits:
            return create_result(
                role=self.role,
                success=False,
                content="No valid edit proposals generated",
                context_for_next=response,
            )

        # Validate all edits
        emit_activity("validate", f"Validating {len(edits)} edit(s)", agent="surgeon")
        valid_edits, validation_errors = self._validate_edits(edits)

        if validation_errors:
            self.log(f"Validation errors: {validation_errors}")

        for edit in valid_edits:
            self._proposed_edits.append(edit)

        if valid_edits:
            files = [e.file_path for e in valid_edits]
            emit_activity(
                "edit",
                f"Proposed {len(valid_edits)} edit(s)",
                files[0] if len(files) == 1 else f"{files[0]} +{len(files)-1} more",
                agent="surgeon",
            )

        edits_proposed = [
            {
                "file": e.file_path,
                "description": e.description,
                "diff_preview": e.diff[:500],
                "content_hash": e.content_hash,
            }
            for e in valid_edits
        ]

        return create_result(
            role=self.role,
            success=len(valid_edits) > 0,
            content=self._format_edits_summary(valid_edits, validation_errors),
            edits_proposed=edits_proposed,
            files_found=[e.file_path for e in valid_edits],
            context_for_next=self._format_for_validator(valid_edits),
        )

    def _needs_more_context(self, task: str, context: str | None) -> bool:
        """
        Determine if Scout collaboration needed.

        Heuristic: Need context if task references code constructs
        (functions, classes, variables) without providing file paths
        or sufficient existing context.
        """
        if not self.can_collaborate():
            return False

        # Substantial context = probably sufficient
        if context and len(context) > 500:
            return False

        # Task references specific existing files we can find
        file_refs = re.findall(
            r"[\w/\\.-]+\.(?:py|js|ts|jsx|tsx|go|rs|java|cpp|c|h)", task
        )
        for ref in file_refs:
            if (self.root_path / ref).exists():
                return False

        # Task mentions code constructs without file context
        code_refs = re.search(
            r"\b(?:function|class|def|method|variable|import)\s+\w+", task, re.I
        )
        if code_refs and not file_refs:
            return True

        return False

    def _gather_additional_context(
        self, task: str, existing_context: str | None
    ) -> str:
        """Ask Scout for related files."""
        self.log("Context sparse, consulting Scout...")

        try:
            scout_result = self.ask_agent(
                AgentRole.SCOUT, f"Find files related to this edit task: {task}"
            )

            if scout_result.success and scout_result.content:
                scout_context = f"## Scout Findings\n{scout_result.content}"
                if existing_context:
                    return f"{existing_context}\n\n{scout_context}"
                return scout_context
        except Exception as e:
            self.log(f"Scout collaboration failed: {e}")

        return existing_context or ""

    def propose_edit(
        self,
        file_path: str,
        find_text: str,
        replace_text: str,
        description: str = "",
    ) -> EditProposal | None:
        """
        Create an edit proposal with content verification.

        Returns None if file doesn't exist or find_text not found.
        Uses fuzzy matching as fallback for whitespace differences.
        """
        # Validate path stays within root (prevents traversal attacks)
        full_path = self._validate_path(file_path)
        if full_path is None:
            return None

        if not full_path.exists():
            self.log(f"File not found: {file_path}")
            return None

        try:
            original = full_path.read_text(encoding="utf-8")

            # Exact match first
            if find_text in original:
                new_content = original.replace(find_text, replace_text, 1)
            else:
                # Fuzzy match for whitespace tolerance
                matched = self._fuzzy_find(original, find_text)
                if matched:
                    new_content = original.replace(matched, replace_text, 1)
                else:
                    self.log(f"Find text not matched in {file_path}")
                    return None

            return EditProposal(
                file_path=file_path,
                original_content=original,
                new_content=new_content,
                description=description or "Edit proposal",
            )

        except Exception as e:
            self.log(f"Error creating edit proposal: {e}")
            return None

    @contextmanager
    def transaction(self) -> Generator[EditTransaction, None, None]:
        """
        Context manager for atomic multi-edit transactions.

        Usage:
            with surgeon.transaction() as tx:
                surgeon.apply_edit(edit1, transaction=tx)
                surgeon.apply_edit(edit2, transaction=tx)
            # Auto-commits on success, auto-rollbacks on exception
        """
        tx = EditTransaction()
        try:
            yield tx
            # Success - record transaction
            self._transaction_history.append(tx)
        except Exception:
            # Failure - rollback all applied edits
            self._rollback_transaction(tx)
            raise

    def apply_edit(
        self,
        edit: EditProposal,
        dry_run: bool = False,
        transaction: EditTransaction | None = None,
    ) -> bool:
        """
        Apply edit with optimistic locking.

        Fails if file content has changed since proposal (prevents
        overwriting concurrent modifications).
        """
        # Validate path (defense in depth - proposal should already be validated)
        full_path = self._validate_path(edit.file_path)
        if full_path is None:
            return False

        # Read current content
        try:
            current_content = full_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.log(f"File disappeared: {edit.file_path}")
            return False

        # Optimistic lock check
        if not edit.verify_unchanged(current_content):
            self.log(f"File modified since proposal: {edit.file_path}")
            return False

        # Syntax validation for Python
        if self.validate_syntax and edit.file_path.endswith(".py"):
            if not self._check_python_syntax(edit.new_content):
                self.log(f"Syntax error in proposed edit: {edit.file_path}")
                return False

        if dry_run:
            self.log(f"[DRY RUN] Would apply edit to {edit.file_path}")
            return True

        # Record backup
        if transaction:
            transaction.record_backup(str(full_path), current_content)
        else:
            self._write_backup(full_path, current_content)

        # Write new content
        try:
            full_path.write_text(edit.new_content, encoding="utf-8")
        except Exception as e:
            self.log(f"Write failed: {e}")
            return False

        # Track
        if transaction:
            transaction.mark_applied(str(full_path))
        self._applied_edits.append(edit)

        # Record in memory
        if self.memory:
            self._record_to_memory(edit)

        self.log(f"Applied edit to {edit.file_path}")
        return True

    def apply_edits(
        self, edits: list[EditProposal], dry_run: bool = False
    ) -> tuple[int, int]:
        """
        Apply multiple edits atomically.

        Returns (success_count, failure_count). On any failure,
        all previously applied edits in this batch are rolled back.
        """
        if dry_run:
            results = [self.apply_edit(e, dry_run=True) for e in edits]
            return sum(results), len(results) - sum(results)

        try:
            with self.transaction() as tx:
                for edit in edits:
                    if not self.apply_edit(edit, transaction=tx):
                        raise RuntimeError(f"Edit failed: {edit.file_path}")
            return len(edits), 0
        except RuntimeError:
            return 0, len(edits)

    def _write_backup(self, path: Path, content: str):
        """Write timestamped backup to temp directory."""
        backup_dir = Path(tempfile.gettempdir()) / "surgeon_backups"
        backup_dir.mkdir(exist_ok=True)

        timestamp = int(time.time() * 1000)
        safe_name = str(path).replace("/", "_").replace("\\", "_")
        backup_path = backup_dir / f"{safe_name}.{timestamp}.bak"

        backup_path.write_text(content, encoding="utf-8")

    def _rollback_transaction(self, tx: EditTransaction):
        """Rollback all applied edits in a transaction."""
        for path in reversed(tx.applied):
            if path in tx.backups:
                try:
                    Path(path).write_text(tx.backups[path], encoding="utf-8")
                    self.log(f"Rolled back: {path}")
                except Exception as e:
                    self.log(f"Rollback failed for {path}: {e}")

    def rollback_last_edit(self) -> bool:
        """Rollback the last applied edit."""
        if not self._applied_edits:
            return False

        edit = self._applied_edits.pop()
        full_path = self._validate_path(edit.file_path)
        if full_path is None:
            self.log(f"Cannot rollback - path validation failed: {edit.file_path}")
            return False

        try:
            full_path.write_text(edit.original_content, encoding="utf-8")
            self.log(f"Rolled back: {edit.file_path}")
            return True
        except Exception as e:
            self.log(f"Rollback failed: {e}")
            return False

    def get_pending_edits(self) -> list[EditProposal]:
        """Get proposed edits not yet applied."""
        applied_ids = {id(e) for e in self._applied_edits}
        return [e for e in self._proposed_edits if id(e) not in applied_ids]

    def _parse_edit_proposals(self, response: str) -> list[EditProposal]:
        """
        Parse edit blocks from LLM response.

        Uses anchored regex to avoid keyword collision issues.
        """
        edits = []

        # Find all edit blocks with proper termination
        for match in re.finditer(
            r"```edit\s*\n"
            r"FILE:\s*(.+?)\s*\n"
            r"FIND:\n(.*?)\n"
            r"REPLACE:\n(.*?)"
            r"\n```",
            response,
            re.DOTALL,
        ):
            file_path = match.group(1).strip()
            find_text = match.group(2)
            replace_text = match.group(3)

            # Remove trailing newline from replace if FIND didn't have one
            if not find_text.endswith("\n") and replace_text.endswith("\n"):
                replace_text = replace_text[:-1]

            edit = self.propose_edit(
                file_path=file_path,
                find_text=find_text,
                replace_text=replace_text,
                description=f"Edit from LLM: {file_path}",
            )

            if edit:
                edits.append(edit)
            else:
                self.log(f"Failed to create edit for {file_path}")

        return edits

    def _validate_edits(
        self, edits: list[EditProposal]
    ) -> tuple[list[EditProposal], list[str]]:
        """
        Validate edits, returning valid edits and error messages.

        Checks:
        - File exists
        - Content changed
        - Syntax valid (Python only)
        - No duplicate file targets (would conflict)
        """
        valid = []
        errors = []
        seen_files: set[str] = set()

        for edit in edits:
            # Duplicate check
            if edit.file_path in seen_files:
                errors.append(f"Duplicate edit target: {edit.file_path}")
                continue
            seen_files.add(edit.file_path)

            # Path validation (prevents traversal) and existence check
            validated_path = self._validate_path(edit.file_path)
            if validated_path is None:
                errors.append(f"Invalid path (traversal blocked): {edit.file_path}")
                continue
            if not validated_path.exists():
                errors.append(f"File not found: {edit.file_path}")
                continue

            # No-op check
            if edit.original_content == edit.new_content:
                errors.append(f"No change: {edit.file_path}")
                continue

            # Syntax check
            if self.validate_syntax and edit.file_path.endswith(".py"):
                if not self._check_python_syntax(edit.new_content):
                    errors.append(f"Syntax error: {edit.file_path}")
                    continue

            valid.append(edit)

        return valid, errors

    def _check_python_syntax(self, code: str) -> bool:
        """Validate Python syntax."""
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False

    def _fuzzy_find(
        self, content: str, target: str, threshold: float = 0.85
    ) -> str | None:
        """
        Find similar text block using sliding window comparison.

        Handles whitespace normalization and minor character differences.
        """
        target_lines = target.strip().split("\n")
        content_lines = content.split("\n")
        window_size = len(target_lines)

        if window_size == 0 or len(content_lines) < window_size:
            return None

        best_match = None
        best_ratio = threshold

        # Normalize for comparison
        def normalize(s: str) -> str:
            return " ".join(s.split())

        target_normalized = "\n".join(normalize(line) for line in target_lines)

        for i in range(len(content_lines) - window_size + 1):
            block_lines = content_lines[i : i + window_size]
            block_normalized = "\n".join(normalize(line) for line in block_lines)

            ratio = difflib.SequenceMatcher(
                None, target_normalized, block_normalized
            ).ratio()

            if ratio > best_ratio:
                best_ratio = ratio
                best_match = "\n".join(block_lines)

        return best_match

    def _record_to_memory(self, edit: EditProposal):
        """Record edit to memory system."""
        try:
            from ..memory import create_edit_from_file_change

            memory_edit = create_edit_from_file_change(
                file_path=edit.file_path,
                original_content=edit.original_content,
                new_content=edit.new_content,
                user_message=edit.description,
            )
            self.memory.record_edit(memory_edit)
        except Exception as e:
            self.log(f"Memory recording failed: {e}")

    def _format_edits_summary(
        self, edits: list[EditProposal], errors: list[str] | None = None
    ) -> str:
        """Format edit summary for output."""
        lines = []

        if errors:
            lines.append(f"## Validation Errors ({len(errors)})")
            for err in errors:
                lines.append(f"- {err}")
            lines.append("")

        if not edits:
            lines.append("No valid edits proposed")
            return "\n".join(lines)

        lines.append(f"## Proposed Edits ({len(edits)})")
        lines.append("")

        for i, edit in enumerate(edits, 1):
            lines.append(f"### {i}. {edit.file_path}")
            lines.append(f"{edit.description}")
            lines.append("")
            lines.append("```diff")
            diff = edit.diff
            if len(diff) > 800:
                lines.append(diff[:800])
                lines.append("... (truncated)")
            else:
                lines.append(diff)
            lines.append("```")
            lines.append("")

        return "\n".join(lines)

    def _format_for_validator(self, edits: list[EditProposal]) -> str:
        """Format edits for validator agent."""
        lines = ["## Edits for Validation", ""]

        for edit in edits:
            lines.append(f"### {edit.file_path}")
            lines.append(f"Hash: {edit.content_hash}")
            lines.append("```diff")
            lines.append(edit.diff[:400])
            lines.append("```")
            lines.append("")

        lines.append("Run tests to validate these changes.")
        return "\n".join(lines)
