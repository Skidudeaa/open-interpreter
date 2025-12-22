> **Fork Notice:** Adds risk-based approval, semantic memory, multi-agent orchestration, and edit validation.
> See [CHANGELOG.md](CHANGELOG.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

# Open Interpreter

LLMs that run code locally. Chat interface in your terminal.

```bash
pip install open-interpreter
interpreter
```

## Quick Start

```python
from interpreter import interpreter

interpreter.chat("Plot AAPL stock prices")  # Single command
interpreter.chat()                           # Interactive mode
```

## New in This Fork

### Risk-Based Approval
```bash
export OPEN_INTERPRETER_APPROVAL=dangerous  # Only prompt for destructive commands
```

### Semantic Memory
```python
interpreter.enable_semantic_memory = True
# Tracks WHY code changed, not just what
# Query: interpreter.semantic_graph.get_institutional_knowledge("file.py")
```

### Multi-Agent Orchestration
```python
from interpreter.core.agents import AgentOrchestrator, WorkflowType

orchestrator = AgentOrchestrator(interpreter)
result = orchestrator.handle_task("Fix the bug", workflow=WorkflowType.EDIT)
```

### Edit Validation
```python
from interpreter.core.validation import EditValidator

validator = EditValidator(project_root=".")
result = validator.validate_edit("file.py", old_code, new_code)
# Syntax check, type check, run related tests, auto-rollback on failure
```

### SDK
```python
from interpreter.sdk import AgentBuilder

builder = AgentBuilder()
scout = builder.from_template("scout")      # Codebase exploration
surgeon = builder.from_template("surgeon")  # Precise edits
swarm = builder.create_swarm([scout, surgeon])
```

### Terminal UI
```bash
export OI_THEME=dark              # dark, light, high-contrast
export OI_UI_DEBUG=true           # Debug logging to ~/.open-interpreter/logs/
```
- Arrow-key menus for code confirmation
- Session autosave on interrupt
- Syntax-highlighted tracebacks
- Auto-format CSV/JSON as tables

## Configuration

```bash
interpreter --model gpt-4                    # Change model
interpreter --local                          # Run locally
interpreter --api_base "http://localhost:1234/v1"  # Custom endpoint
```

```python
interpreter.llm.model = "claude-3"
interpreter.auto_run = True                  # Skip confirmations
interpreter.system_message += "Custom instructions..."
```

## Docs

| Topic | Location |
|-------|----------|
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Changelog | [CHANGELOG.md](CHANGELOG.md) |
| Dev History | [docs/DEVELOPMENT_HISTORY.md](docs/DEVELOPMENT_HISTORY.md) |
| Contributing | [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) |
| Full Docs | [docs.openinterpreter.com](https://docs.openinterpreter.com/) |

## Safety

Code runs locally. Can modify files and system settings.

- Default: Asks confirmation before running code
- `interpreter -y` or `interpreter.auto_run = True` bypasses this
- Use `OPEN_INTERPRETER_APPROVAL=dangerous` for smart filtering

---

[Discord](https://discord.gg/Hvz9Axh84z) · [Desktop App](https://0ggfznkwh4j.typeform.com/to/G21i9lJ2) · Not affiliated with OpenAI
