#!/bin/bash
# Install this fork of Open Interpreter

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Installing Open Interpreter Fork ==="

# Create venv
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$SCRIPT_DIR/venv"
fi

# Install
source "$SCRIPT_DIR/venv/bin/activate"
echo "Installing dependencies..."
pip install -q -e "$SCRIPT_DIR"
pip install -q pyautogui

echo ""
echo "Done! Add to ~/.bashrc:"
echo ""
echo '  export OPEN_INTERPRETER_APPROVAL=dangerous'
echo '  alias oi="source '"$SCRIPT_DIR"'/venv/bin/activate && interpreter"'
echo ""
echo "Then: source ~/.bashrc && oi"
