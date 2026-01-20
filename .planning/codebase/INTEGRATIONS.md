# External Integrations

**Analysis Date:** 2026-01-19

## APIs & External Services

### LLM Providers (via LiteLLM)

All LLM calls route through LiteLLM (`interpreter/core/llm/llm.py`), supporting 100+ providers:

**Primary:**
- OpenAI - `OPENAI_API_KEY`
  - SDK: litellm
  - Models: GPT-4o, GPT-4, etc.

- Anthropic - `ANTHROPIC_API_KEY`
  - SDK: litellm + anthropic (direct for computer-use)
  - Models: Claude 3.5 Sonnet, Claude 3 Opus
  - Computer Use: `interpreter/computer_use/loop.py`

- Google Gemini - `GEMINI_API_KEY` or `GOOGLE_API_KEY`
  - SDK: litellm + google-generativeai
  - Default model: `gemini/gemini-3-flash-preview`

**Secondary:**
- OpenRouter - `OPENROUTER_API_KEY`
  - Used for intent refinement
  - File: `interpreter/core/intent_refiner.py`

- Ollama - Local model serving
  - Host: `OLLAMA_HOST` (default: `http://localhost:11434`)
  - Auto-downloads models on first use
  - File: `interpreter/core/llm/llm.py:378-432`

- AWS Bedrock - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION_NAME`
  - Profile: `interpreter/terminal_interface/profiles/defaults/bedrock-anthropic.py`

- Cerebras - `CEREBRAS_API_KEY`
  - Profile: `interpreter/terminal_interface/profiles/defaults/cerebras.py`

- Groq - `GROQ_API_KEY`
  - Profile: `interpreter/terminal_interface/profiles/defaults/groq.py`

### Search Providers

Located in `interpreter/core/computer/search/providers/`:

**Tavily (AI-optimized):**
- Env: `TAVILY_API_KEY`
- SDK: requests (direct API)
- File: `interpreter/core/computer/search/providers/tavily.py`
- Features: AI answers, domain filtering, citations

**Google Custom Search:**
- Env: `GOOGLE_API_KEY`, `GOOGLE_SEARCH_ENGINE_ID`
- SDK: requests (direct API)
- File: `interpreter/core/computer/search/providers/google.py`

**DuckDuckGo (Free):**
- No API key required
- SDK: ddgs or duckduckgo-search
- File: `interpreter/core/computer/search/providers/duckduckgo.py`

## Data Storage

### Databases

**Semantic Memory (DuckDB/SQLite):**
- Primary: DuckDB `^1.0.0` (optional, faster)
- Fallback: SQLite (built-in)
- File: `interpreter/core/memory/semantic_graph.py`
- Purpose: Edit history, conversation linking, symbol tracking
- Storage: User-configurable path or in-memory

### File Storage

- Local filesystem only
- Temp files for code execution
- User home directory for settings/logs:
  - `~/.config/open-interpreter/` - Settings, profiles
  - `~/.open-interpreter/` - Logs

### Caching

- In-memory search result caching: `interpreter/core/computer/search/cache.py`
- No external cache service

## Authentication & Identity

**Auth Provider:**
- Custom token-based for server mode
- File: `interpreter/core/async_core.py`

**Environment Variables:**
- `INTERPRETER_API_KEY` - Server authentication
- `INTERPRETER_REQUIRE_AUTH` - Enable auth requirement
- `INTERPRETER_REQUIRE_ACKNOWLEDGE` - Output acknowledgment

**Implementation:**
- No external auth provider integration
- API key validation in server routes

## Monitoring & Observability

### Error Tracking

- None (external service)
- Errors logged to console and log files

### Logs

- File logging to `~/.open-interpreter/logs/` (when `OI_UI_DEBUG=true`)
- Logger: `interpreter/terminal_interface/utils/ui_logger.py`
- Console output via Rich library

### Telemetry

- Optional telemetry: `interpreter/core/utils/telemetry.py`
- Disabled via `DISABLE_TELEMETRY=true`

## CI/CD & Deployment

### Hosting

- Self-hosted / local execution
- Server mode via FastAPI/Uvicorn

### CI Pipeline

- Pre-commit hooks configured (`pre-commit ^3.5.0`)
- GitHub workflows (`.github/` directory)

## Environment Configuration

### Required Env Vars (for full functionality)

```bash
# LLM (at least one required)
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...

# Optional feature activation
OI_ACTIVATE_ALL=true
```

### Optional Env Vars

```bash
# Search
TAVILY_API_KEY=...
GOOGLE_API_KEY=...
GOOGLE_SEARCH_ENGINE_ID=...

# Server mode
INTERPRETER_API_KEY=...
INTERPRETER_HOST=...
INTERPRETER_PORT=...
INTERPRETER_CORS_ORIGINS=...

# Local models
OLLAMA_HOST=http://localhost:11434

# Intent refinement
OPENROUTER_API_KEY=...
OI_UNSTEER_MODEL=...
OI_ENABLE_UNSTEER=true

# Code execution
OPEN_INTERPRETER_APPROVAL=dangerous
OPEN_INTERPRETER_AUTO_APPROVE=true

# UI/Debug
OI_UI_DEBUG=true
OI_THEME=dark
NO_TUI=true
DISABLE_TELEMETRY=true
```

### Secrets Location

- Environment variables (standard approach)
- No secrets file management
- No vault integration

## Browser Automation

**Selenium/Chrome:**
- SDK: selenium ^4.24.0, webdriver-manager ^4.0.2
- File: `interpreter/core/computer/browser/browser.py`
- Purpose: Web scraping, page navigation, form filling
- Requirements: Chrome/Chromium browser

## Model Context Protocol (MCP)

**MCP Bridge:**
- File: `interpreter/sdk/mcp_bridge.py`
- Purpose: Two-way MCP integration
  - Consume external MCP tools
  - Expose agents as MCP servers
- Transports: STDIO, HTTP, SSE

## Webhooks & Callbacks

### Incoming

- WebSocket endpoints in server mode
- File: `interpreter/core/async_core.py`
- Routes: `/ws` for streaming chat

### Outgoing

- None implemented
- Plugin hooks for custom integrations: `interpreter/sdk/plugins.py`

## Plugin System

**Extension Points:**
- `on_before_execute` - Modify context before execution
- `on_after_execute` - Process results
- `on_before_llm` / `on_after_llm` - LLM call interception
- `on_before_edit` / `on_after_edit` - Edit validation
- `on_error` - Error handling
- `on_tool_call` - Tool invocation

File: `interpreter/sdk/plugins.py`

## Profile System

**Default Profiles:**
- Location: `interpreter/terminal_interface/profiles/defaults/`
- Format: Python (.py) or YAML (.yaml/.yml)
- Purpose: Pre-configured setups for specific use cases

Available profiles:
- `default.yaml` - Standard configuration
- `bedrock-anthropic.py` - AWS Bedrock setup
- `cerebras.py` - Cerebras API
- `groq.py` - Groq API
- `llama31-database.py` - Database operations with Llama
- `obsidian.py` - Obsidian vault integration
- `snowpark.yml` - Snowflake Snowpark
- `aws-docs.py` - AWS documentation search

---

*Integration audit: 2026-01-19*
