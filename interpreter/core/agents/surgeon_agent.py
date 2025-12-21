"""
SurgeonAgent - Precise code editing agent.

Optimized for making minimal, correct code changes.
Uses context from Scout and Architect agents to make targeted edits.

Capabilities:
- String replacement edits
- Function/class additions
- Import management
- Code refactoring
"""

import os
import difflib
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass

from .base_agent import BaseAgent, AgentRole, AgentResult, create_result


@dataclass
class EditProposal:
    """A proposed code edit."""
    file_path: str
    original_content: str
    new_content: str
    description: str
    confidence: float = 0.8

    @property
    def diff(self) -> str:
        """Generate unified diff."""
        return "\n".join(difflib.unified_diff(
            self.original_content.splitlines(keepends=True),
            self.new_content.splitlines(keepends=True),
            fromfile=f"a/{self.file_path}",
            tofile=f"b/{self.file_path}",
        ))


class SurgeonAgent(BaseAgent):
    """
    Agent for making precise code edits.

    Takes context from other agents and produces targeted,
    minimal code changes.
    """

    role = AgentRole.SURGEON

    def __init__(
        self,
        interpreter,
        memory=None,
        root_path: Optional[str] = None,
        validate_syntax: bool = True,
    ):
        super().__init__(interpreter, memory)
        self.root_path = root_path or os.getcwd()
        self.validate_syntax = validate_syntax

        # Track proposed and applied edits
        self._proposed_edits: List[EditProposal] = []
        self._applied_edits: List[EditProposal] = []

    def get_system_message(self) -> str:
        return """You are a Surgeon Agent specialized in precise code editing.

Your job is to make minimal, correct code changes. You should:
1. Make the smallest change that accomplishes the goal
2. Preserve existing code style and conventions
3. Not add unnecessary changes or "improvements"
4. Validate your edits won't break anything

When editing:
- Use string replacement for small changes
- Preserve exact indentation
- Don't add extra comments unless requested
- Don't refactor unless specifically asked

Always output your edits in this format:
```edit
FILE: path/to/file.py
FIND:
<exact text to find>
REPLACE:
<replacement text>
```

You can propose multiple edits in one response."""

    def execute(self, task: str, context: Optional[str] = None) -> AgentResult:
        """
        Execute a surgical edit task.

        Args:
            task: The edit task description
            context: Context from Scout/Architect agents

        Returns:
            AgentResult with proposed edits
        """
        self.log(f"Starting surgical edit: {task[:50]}...")

        # Build messages with context
        messages = self.prepare_messages(task, context)

        # Get LLM response with edit proposals
        response = self.run_interpreter(messages)

        # Parse edit proposals from response
        edits = self._parse_edit_proposals(response)

        if not edits:
            return create_result(
                role=self.role,
                success=False,
                content="No valid edit proposals generated",
                context_for_next=response,
            )

        # Validate edits
        valid_edits = []
        for edit in edits:
            if self._validate_edit(edit):
                valid_edits.append(edit)
                self._proposed_edits.append(edit)

        # Format edits for result
        edits_proposed = [
            {
                "file": e.file_path,
                "description": e.description,
                "diff_preview": e.diff[:500],
            }
            for e in valid_edits
        ]

        return create_result(
            role=self.role,
            success=len(valid_edits) > 0,
            content=self._format_edits_summary(valid_edits),
            edits_proposed=edits_proposed,
            files_found=[e.file_path for e in valid_edits],
            context_for_next=self._format_for_validator(valid_edits),
        )

    def propose_edit(
        self,
        file_path: str,
        find_text: str,
        replace_text: str,
        description: str = "",
    ) -> Optional[EditProposal]:
        """
        Create an edit proposal.

        Args:
            file_path: Path to the file
            find_text: Text to find
            replace_text: Text to replace with
            description: Description of the edit

        Returns:
            EditProposal or None if file doesn't exist
        """
        full_path = os.path.join(self.root_path, file_path)

        if not os.path.exists(full_path):
            return None

        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                original = f.read()

            if find_text not in original:
                # Try fuzzy matching
                find_text = self._fuzzy_find(original, find_text)
                if not find_text:
                    return None

            new_content = original.replace(find_text, replace_text, 1)

            return EditProposal(
                file_path=file_path,
                original_content=original,
                new_content=new_content,
                description=description,
            )

        except Exception as e:
            self.log(f"Error creating edit proposal: {e}")
            return None

    def apply_edit(self, edit: EditProposal, dry_run: bool = False) -> bool:
        """
        Apply an edit to the filesystem.

        Args:
            edit: The edit to apply
            dry_run: If True, don't actually write

        Returns:
            True if successful
        """
        full_path = os.path.join(self.root_path, edit.file_path)

        # Validate syntax if it's Python
        if self.validate_syntax and edit.file_path.endswith('.py'):
            if not self._check_python_syntax(edit.new_content):
                self.log(f"Syntax error in proposed edit for {edit.file_path}")
                return False

        if dry_run:
            self.log(f"[DRY RUN] Would apply edit to {edit.file_path}")
            return True

        try:
            # Create backup
            backup_path = full_path + ".bak"
            if os.path.exists(full_path):
                with open(full_path, 'r') as f:
                    backup_content = f.read()
                with open(backup_path, 'w') as f:
                    f.write(backup_content)

            # Write new content
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(edit.new_content)

            self._applied_edits.append(edit)

            # Record in memory if available
            if self.memory:
                from ..memory import create_edit_from_file_change
                memory_edit = create_edit_from_file_change(
                    file_path=edit.file_path,
                    original_content=edit.original_content,
                    new_content=edit.new_content,
                    user_message=edit.description,
                )
                self.memory.record_edit(memory_edit)

            self.log(f"Applied edit to {edit.file_path}")
            return True

        except Exception as e:
            self.log(f"Error applying edit: {e}")
            return False

    def rollback_last_edit(self) -> bool:
        """
        Rollback the last applied edit.

        Returns:
            True if successful
        """
        if not self._applied_edits:
            return False

        edit = self._applied_edits.pop()
        full_path = os.path.join(self.root_path, edit.file_path)

        try:
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(edit.original_content)

            self.log(f"Rolled back edit to {edit.file_path}")
            return True

        except Exception as e:
            self.log(f"Error rolling back: {e}")
            return False

    def get_pending_edits(self) -> List[EditProposal]:
        """Get list of proposed but not yet applied edits."""
        applied_set = set(id(e) for e in self._applied_edits)
        return [e for e in self._proposed_edits if id(e) not in applied_set]

    def _parse_edit_proposals(self, response: str) -> List[EditProposal]:
        """
        Parse edit proposals from LLM response.

        Expects format:
        ```edit
        FILE: path/to/file.py
        FIND:
        <text>
        REPLACE:
        <text>
        ```
        """
        edits = []

        # Split by edit blocks
        import re
        edit_blocks = re.findall(
            r'```edit\n(.*?)```',
            response,
            re.DOTALL
        )

        for block in edit_blocks:
            try:
                # Parse FILE
                file_match = re.search(r'FILE:\s*(.+?)(?:\n|$)', block)
                if not file_match:
                    continue
                file_path = file_match.group(1).strip()

                # Parse FIND and REPLACE
                find_match = re.search(r'FIND:\n(.*?)(?:REPLACE:|$)', block, re.DOTALL)
                replace_match = re.search(r'REPLACE:\n(.*?)$', block, re.DOTALL)

                if not find_match or not replace_match:
                    continue

                find_text = find_match.group(1).rstrip('\n')
                replace_text = replace_match.group(1).rstrip('\n')

                edit = self.propose_edit(
                    file_path=file_path,
                    find_text=find_text,
                    replace_text=replace_text,
                    description=f"Edit from LLM response",
                )

                if edit:
                    edits.append(edit)

            except Exception as e:
                self.log(f"Error parsing edit block: {e}")
                continue

        return edits

    def _validate_edit(self, edit: EditProposal) -> bool:
        """Validate an edit proposal."""
        # Check file exists
        full_path = os.path.join(self.root_path, edit.file_path)
        if not os.path.exists(full_path):
            return False

        # Check content actually changed
        if edit.original_content == edit.new_content:
            return False

        # Check syntax for Python files
        if self.validate_syntax and edit.file_path.endswith('.py'):
            if not self._check_python_syntax(edit.new_content):
                return False

        return True

    def _check_python_syntax(self, code: str) -> bool:
        """Check if Python code has valid syntax."""
        import ast
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False

    def _fuzzy_find(self, content: str, target: str, threshold: float = 0.8) -> Optional[str]:
        """
        Find similar text in content using fuzzy matching.

        Args:
            content: Content to search in
            target: Target text to find
            threshold: Similarity threshold (0-1)

        Returns:
            Matching text or None
        """
        target_lines = target.strip().split('\n')
        content_lines = content.split('\n')

        # Try to find a matching block
        for i in range(len(content_lines) - len(target_lines) + 1):
            block = '\n'.join(content_lines[i:i + len(target_lines)])

            # Calculate similarity
            matcher = difflib.SequenceMatcher(None, target, block)
            if matcher.ratio() >= threshold:
                return block

        return None

    def _format_edits_summary(self, edits: List[EditProposal]) -> str:
        """Format edit proposals as a summary."""
        if not edits:
            return "No edits proposed"

        lines = [f"## Proposed Edits ({len(edits)})", ""]

        for i, edit in enumerate(edits, 1):
            lines.append(f"### Edit {i}: {edit.file_path}")
            lines.append(f"Description: {edit.description}")
            lines.append("")
            lines.append("```diff")
            lines.append(edit.diff[:1000])
            if len(edit.diff) > 1000:
                lines.append("... (truncated)")
            lines.append("```")
            lines.append("")

        return "\n".join(lines)

    def _format_for_validator(self, edits: List[EditProposal]) -> str:
        """Format edits for the validator agent."""
        lines = ["## Edits for Validation", ""]

        for edit in edits:
            lines.append(f"### {edit.file_path}")
            lines.append("Changes:")
            lines.append("```diff")
            lines.append(edit.diff[:500])
            lines.append("```")
            lines.append("")

        lines.append("Please validate these edits by running relevant tests.")

        return "\n".join(lines)
