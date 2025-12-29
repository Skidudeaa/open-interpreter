"""
Terminal Interface Package

Suppress prompt_toolkit CPR warning for terminals that don't support it.
WHY: tmux, Warp, Cursor, Tabby, VS Code terminals trigger this harmless warning.
Must be set before prompt_toolkit is imported anywhere in the package.
"""

import os
import warnings

os.environ.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")
warnings.filterwarnings("ignore", message=".*cursor position.*", category=UserWarning)
