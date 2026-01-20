# Technology Stack

**Analysis Date:** 2026-01-19

## Languages

**Primary:**
- Python 3.9-3.12 - All application code, CLI, SDK

**Secondary:**
- Shell/Bash - Subprocess execution, user commands
- JavaScript/Node.js - Subprocess language support
- PowerShell - Windows subprocess support
- AppleScript - macOS automation
- Ruby, R, Java, HTML/React - Additional language runners

## Runtime

**Environment:**
- Python 3.9+ (requires `>=3.9,<3.13`)
- CPython standard interpreter

**Package Manager:**
- Poetry 2.2.1
- Lockfile: `poetry.lock` present

## Frameworks

**Core:**
- LiteLLM ^1.41.26 - Unified LLM interface (100+ model providers)
- Pydantic ^2.6.4 - Data validation and settings management
- Rich >=13.4.2,<14.0.0 - Terminal formatting and output

**API/Server:**
- FastAPI ^0.111.0 - Async REST API server
- Uvicorn ^0.30.1 - ASGI server
- Starlette ^0.37.2 - HTTP/WebSocket primitives

**CLI/Terminal:**
- prompt_toolkit ^3.0.0 - Interactive terminal input
- Typer ^0.12.5 - CLI argument parsing
- Yaspin ^3.0.2 - Terminal spinners
- Inquirer ^3.1.3 - Interactive prompts

**Testing:**
- pytest ^7.4.0 - Test framework
- pytest-cov ^4.1.0 - Coverage reporting

**Build/Dev:**
- Black ^23.10.1 - Code formatting
- isort ^5.12.0 - Import sorting
- pre-commit ^3.5.0 - Git hooks
- Ruff - Linting (configured in pyproject.toml)

## Key Dependencies

**Critical:**
- `litellm ^1.41.26` - All LLM calls route through this. Supports OpenAI, Anthropic, Google, Ollama, Bedrock, etc.
- `anthropic ^0.37.1` - Direct Anthropic SDK for computer-use features
- `google-generativeai ^0.7.1` - Google Gemini integration
- `tiktoken ^0.7.0` - Token counting for context management

**Infrastructure:**
- `jupyter-client ^8.6.0` - Jupyter kernel for Python execution
- `ipykernel ^6.26.0` - IPython kernel for code execution
- `selenium ^4.24.0` - Browser automation
- `webdriver-manager ^4.0.2` - ChromeDriver management

**Data/Storage (Optional):**
- `duckdb ^1.0.0` - Semantic memory database (optional, falls back to SQLite)
- `PyMuPDF ^1.23.0` - PDF processing (documents extra)

**UI/Terminal:**
- `pyautogui ^0.9.54` - GUI automation for OS mode
- `pyperclip ^1.9.0` - Clipboard operations
- `html2text ^2024.2.26` - HTML to text conversion
- `html2image ^2.0.4.3` - HTML screenshot generation

## Optional Extras

Defined in `pyproject.toml`:

- `[os]` - Computer vision, screen control: opencv-python, pytesseract, screeninfo, sentence-transformers
- `[local]` - Local model support: torch, transformers, torchvision, easyocr
- `[safe]` - Code security scanning: semgrep
- `[server]` - Server mode: fastapi, uvicorn, janus
- `[memory]` - Semantic memory: duckdb
- `[documents]` - Document processing: PyMuPDF, python-docx, trafilatura
- `[search]` - Web search: ddgs, aiohttp

## Configuration

**Environment Variables:**
- `OI_ACTIVATE_ALL=true` - Enable all advanced features
- `OI_UI_DEBUG=true` - Debug logging to `~/.open-interpreter/logs/`
- `OI_NO_TUI=true` or `NO_TUI=true` - Disable interactive mode
- `OI_THEME` - UI theme (dark/light)
- `OPEN_INTERPRETER_APPROVAL` - Risk-based approval (off/dangerous/all)
- `LITELLM_LOCAL_MODEL_COST_MAP=True` - Set automatically by the app

**LLM Configuration:**
- Default model: `gemini/gemini-3-flash-preview`
- API keys via standard env vars: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`

**Persistent Storage:**
- Settings: `~/.config/open-interpreter/settings.json`
- Logs: `~/.open-interpreter/logs/`
- Profiles: `~/.config/open-interpreter/profiles/`

**Build Configuration:**
- `pyproject.toml` - Primary config (Poetry, Black, isort, Ruff)
- Target Python: 3.9+
- Line length: 88 (Black default)

## Platform Requirements

**Development:**
- Python 3.9-3.12
- Poetry for dependency management
- Git for version control
- Optional: Chrome/Chromium for browser automation

**Production:**
- Python 3.9+ runtime
- OS: Linux, macOS, Windows (with pyreadline3)
- For OS mode: Display server, pyautogui dependencies
- For server mode: FastAPI, Uvicorn

**Entry Points (from pyproject.toml):**
```
interpreter = interpreter.terminal_interface.start_terminal_interface:main
i = interpreter.terminal_interface.start_terminal_interface:main
wtf = scripts.wtf:main
```

---

*Stack analysis: 2026-01-19*
