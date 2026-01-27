"""
Textual Widgets for Open Interpreter TUI

Phase 0-3: Widgets for Textual-based terminal interface.
These widgets replace Rich-based components with Textual's reactive model.

Migration path:
- CodeBlockWidget replaces components/code_block.py
- MessageWidget replaces components/message_block.py
- AgentStripWidget replaces components/agent_strip.py
- OutputPanel replaces pt_app.py output area
- ContextPanelWidget replaces components/context_panel.py
- AgentTreeWidget replaces components/agent_tree.py
"""

from .agent_tree import AgentTreeWidget
from .code_block import CodeBlockWidget
from .context_panel import ContextPanelWidget
from .input_area import InputArea
from .message_block import MessageWidget
from .output_panel import OutputPanel

__all__ = [
    "AgentTreeWidget",
    "CodeBlockWidget",
    "ContextPanelWidget",
    "InputArea",
    "MessageWidget",
    "OutputPanel",
]
