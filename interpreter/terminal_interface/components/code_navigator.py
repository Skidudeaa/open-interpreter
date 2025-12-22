"""
Code Navigator - Block navigation and fold/unfold support.

Provides navigation between code blocks and fold/unfold functionality
for output sections. Integrates with ConversationState for tracking.

Part of Phase 3: Context Panel
"""

from typing import Optional, List, Callable
from dataclasses import dataclass, field
from enum import Enum, auto

from .ui_state import UIState, UIMode


class BlockType(Enum):
    """Types of navigable blocks"""
    MESSAGE = auto()    # User or assistant message
    CODE = auto()       # Code block
    OUTPUT = auto()     # Code output
    ERROR = auto()      # Error output


@dataclass
class NavigableBlock:
    """
    Represents a navigable block in the conversation.

    Used for j/k navigation and fold/unfold tracking.
    """
    id: str                          # Unique block ID
    block_type: BlockType            # Type of block
    index: int                       # Position in conversation
    is_folded: bool = False          # True if output is collapsed
    line_count: int = 0              # Number of lines (for output)
    preview_lines: int = 3           # Lines to show when folded
    parent_id: Optional[str] = None  # Parent block (e.g., code block for output)


class CodeNavigator:
    """
    Manages navigation between code blocks and fold/unfold state.

    Features:
    - j/k navigation between blocks
    - Space to toggle fold/unfold on output
    - y to copy selected block to clipboard
    - Enter to re-run selected code block

    Key bindings (handled by InputHandler):
    - j or ↓: Select next block
    - k or ↑: Select previous block
    - Space: Toggle fold/unfold on output
    - y: Copy selected block
    - Enter: Re-run code block (with confirmation)
    """

    def __init__(self, state: UIState):
        """
        Initialize the code navigator.

        Args:
            state: The UIState instance
        """
        self.state = state
        self._blocks: List[NavigableBlock] = []
        self._block_id_counter = 0

        # Callbacks for actions
        self._on_selection_change: Optional[Callable[[Optional[str]], None]] = None
        self._on_fold_change: Optional[Callable[[str, bool], None]] = None
        self._on_copy_request: Optional[Callable[[str], None]] = None
        self._on_rerun_request: Optional[Callable[[str], None]] = None

    @property
    def selected_index(self) -> int:
        """Get current selection index from UIState."""
        return self.state.conversation.current_block_index

    @selected_index.setter
    def selected_index(self, value: int):
        """Set current selection index in UIState."""
        self.state.conversation.current_block_index = value

    @property
    def selected_block(self) -> Optional[NavigableBlock]:
        """Get the currently selected block."""
        if 0 <= self.selected_index < len(self._blocks):
            return self._blocks[self.selected_index]
        return None

    @property
    def block_count(self) -> int:
        """Get total number of navigable blocks."""
        return len(self._blocks)

    def register_block(
        self,
        block_type: BlockType,
        line_count: int = 0,
        parent_id: Optional[str] = None,
    ) -> str:
        """
        Register a new navigable block.

        Args:
            block_type: Type of block
            line_count: Number of lines (for fold calculation)
            parent_id: Parent block ID (for output blocks)

        Returns:
            Unique block ID
        """
        self._block_id_counter += 1
        block_id = f"block-{self._block_id_counter}"

        block = NavigableBlock(
            id=block_id,
            block_type=block_type,
            index=len(self._blocks),
            line_count=line_count,
            parent_id=parent_id,
        )

        # Auto-fold long outputs
        if block_type == BlockType.OUTPUT and line_count > 20:
            block.is_folded = True

        self._blocks.append(block)
        return block_id

    def update_block(self, block_id: str, line_count: int = None, is_folded: bool = None):
        """
        Update a block's properties.

        Args:
            block_id: Block ID to update
            line_count: New line count
            is_folded: New fold state
        """
        for block in self._blocks:
            if block.id == block_id:
                if line_count is not None:
                    block.line_count = line_count
                    # Auto-fold if now too long
                    if line_count > 20 and is_folded is None:
                        block.is_folded = True
                if is_folded is not None:
                    block.is_folded = is_folded
                break

    def clear(self):
        """Clear all blocks (e.g., on new conversation)."""
        self._blocks.clear()
        self._block_id_counter = 0
        self.selected_index = 0

    # Navigation methods

    def select_next(self) -> bool:
        """
        Move selection to next block.

        Returns:
            True if selection changed
        """
        if self.selected_index < len(self._blocks) - 1:
            self.selected_index += 1
            self._notify_selection_change()
            return True
        return False

    def select_prev(self) -> bool:
        """
        Move selection to previous block.

        Returns:
            True if selection changed
        """
        if self.selected_index > 0:
            self.selected_index -= 1
            self._notify_selection_change()
            return True
        return False

    def select_first(self):
        """Select the first block."""
        if self._blocks:
            self.selected_index = 0
            self._notify_selection_change()

    def select_last(self):
        """Select the last block."""
        if self._blocks:
            self.selected_index = len(self._blocks) - 1
            self._notify_selection_change()

    def select_by_id(self, block_id: str) -> bool:
        """
        Select a block by its ID.

        Args:
            block_id: Block ID to select

        Returns:
            True if block was found and selected
        """
        for i, block in enumerate(self._blocks):
            if block.id == block_id:
                self.selected_index = i
                self._notify_selection_change()
                return True
        return False

    def select_next_code(self) -> bool:
        """
        Move selection to next code block (skip messages/output).

        Returns:
            True if a code block was found
        """
        for i in range(self.selected_index + 1, len(self._blocks)):
            if self._blocks[i].block_type == BlockType.CODE:
                self.selected_index = i
                self._notify_selection_change()
                return True
        return False

    def select_prev_code(self) -> bool:
        """
        Move selection to previous code block.

        Returns:
            True if a code block was found
        """
        for i in range(self.selected_index - 1, -1, -1):
            if self._blocks[i].block_type == BlockType.CODE:
                self.selected_index = i
                self._notify_selection_change()
                return True
        return False

    # Fold/unfold methods

    def toggle_fold(self) -> bool:
        """
        Toggle fold state of selected block.

        Returns:
            True if fold state was changed
        """
        block = self.selected_block
        if block and block.block_type in (BlockType.OUTPUT, BlockType.ERROR):
            block.is_folded = not block.is_folded
            self._notify_fold_change(block.id, block.is_folded)
            return True
        return False

    def fold_selected(self):
        """Fold the selected block."""
        block = self.selected_block
        if block and not block.is_folded:
            block.is_folded = True
            self._notify_fold_change(block.id, True)

    def unfold_selected(self):
        """Unfold the selected block."""
        block = self.selected_block
        if block and block.is_folded:
            block.is_folded = False
            self._notify_fold_change(block.id, False)

    def fold_all(self):
        """Fold all output blocks."""
        for block in self._blocks:
            if block.block_type in (BlockType.OUTPUT, BlockType.ERROR) and not block.is_folded:
                block.is_folded = True
                self._notify_fold_change(block.id, True)

    def unfold_all(self):
        """Unfold all output blocks."""
        for block in self._blocks:
            if block.is_folded:
                block.is_folded = False
                self._notify_fold_change(block.id, False)

    def is_folded(self, block_id: str) -> bool:
        """Check if a block is folded."""
        for block in self._blocks:
            if block.id == block_id:
                return block.is_folded
        return False

    # Action methods

    def copy_selected(self):
        """Request copy of selected block content."""
        block = self.selected_block
        if block and self._on_copy_request:
            self._on_copy_request(block.id)

    def rerun_selected(self):
        """Request re-run of selected code block."""
        block = self.selected_block
        if block and block.block_type == BlockType.CODE and self._on_rerun_request:
            self._on_rerun_request(block.id)

    # Callback setters

    def set_selection_handler(self, handler: Callable[[Optional[str]], None]):
        """Set callback for selection changes."""
        self._on_selection_change = handler

    def set_fold_handler(self, handler: Callable[[str, bool], None]):
        """Set callback for fold state changes."""
        self._on_fold_change = handler

    def set_copy_handler(self, handler: Callable[[str], None]):
        """Set callback for copy requests."""
        self._on_copy_request = handler

    def set_rerun_handler(self, handler: Callable[[str], None]):
        """Set callback for re-run requests."""
        self._on_rerun_request = handler

    # Internal notification methods

    def _notify_selection_change(self):
        """Notify listeners of selection change."""
        if self._on_selection_change:
            block = self.selected_block
            self._on_selection_change(block.id if block else None)

    def _notify_fold_change(self, block_id: str, is_folded: bool):
        """Notify listeners of fold state change."""
        if self._on_fold_change:
            self._on_fold_change(block_id, is_folded)

    # Query methods

    def get_visible_lines(self, block_id: str) -> int:
        """
        Get number of visible lines for a block.

        Args:
            block_id: Block ID

        Returns:
            Number of visible lines (preview if folded, all if not)
        """
        for block in self._blocks:
            if block.id == block_id:
                if block.is_folded:
                    return min(block.preview_lines, block.line_count)
                return block.line_count
        return 0

    def get_block_by_id(self, block_id: str) -> Optional[NavigableBlock]:
        """Get a block by its ID."""
        for block in self._blocks:
            if block.id == block_id:
                return block
        return None

    def get_blocks_by_type(self, block_type: BlockType) -> List[NavigableBlock]:
        """Get all blocks of a specific type."""
        return [b for b in self._blocks if b.block_type == block_type]

    def get_status_text(self) -> str:
        """
        Get status text for display (e.g., "Block 3/10 (folded)").

        Returns:
            Status text string
        """
        if not self._blocks:
            return "No blocks"

        block = self.selected_block
        if not block:
            return "No selection"

        status = f"Block {self.selected_index + 1}/{len(self._blocks)}"

        if block.block_type == BlockType.CODE:
            status += " [code]"
        elif block.block_type == BlockType.OUTPUT:
            if block.is_folded:
                status += f" [output, folded {block.preview_lines}/{block.line_count}]"
            else:
                status += f" [output, {block.line_count} lines]"
        elif block.block_type == BlockType.ERROR:
            status += " [error]"

        return status
