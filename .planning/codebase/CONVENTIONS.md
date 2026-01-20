# Coding Conventions

**Analysis Date:** 2026-01-19

## Naming Patterns

**Files:**
- snake_case for Python modules: `core.py`, `respond.py`, `semantic_graph.py`
- Test files prefixed with `test_`: `test_interpreter.py`, `test_agent_system.py`
- `__init__.py` for package exports
- Config files use standard names: `pyproject.toml`, `.pre-commit-config.yaml`

**Functions:**
- snake_case for all functions: `def capture_source_file_states()`, `def _build_system_message()`
- Private/internal functions prefixed with underscore: `_detect_headless()`, `_get_memory_module()`
- Test functions prefixed with `test_`: `def test_valid_python_syntax()`

**Variables:**
- snake_case for local variables: `interpreter_id`, `cache_key`, `model_values`
- UPPER_CASE for module constants: `_OI_ACTIVATE_ALL`, `_PROJECT_MARKERS`, `MAX_AGENT_CALL_DEPTH`
- Private module variables prefixed with underscore: `_memory_module`, `_settings_cache`

**Classes:**
- PascalCase for class names: `OpenInterpreter`, `SemanticEditGraph`, `AgentOrchestrator`
- Enum classes follow same pattern: `WorkflowType`, `EventType`, `UIMode`
- Dataclasses use same convention: `AgentState`, `ValidationResult`, `WorkflowResult`

**Type Hints:**
- Used throughout codebase for function signatures
- Pattern: `def _get_refined_message(interpreter, content: str) -> str:`
- Optional types: `Optional["SemanticEditGraph"]`, `db_path: str | None = None`
- Use `TYPE_CHECKING` guard for import-only types to avoid circular imports

## Code Style

**Formatting:**
- Black formatter (version 23.10.1)
- 88 character line limit (Black default)
- Target Python version: 3.9
- Double quotes for strings

**Linting:**
- Ruff for linting (v0.8.6 via pre-commit)
- isort with black profile for import sorting
- Multi-line output style 3 with trailing commas
- Ignored rules:
  - `E501`: Line too long (Black handles this)
  - `B008`: Function calls in argument defaults
  - `B905`: zip without strict=
  - `F401` in `__init__.py`: Unused imports OK for re-exports
  - `B011` in tests: assert False OK

**Pre-commit Hooks (`.pre-commit-config.yaml`):**
```yaml
- ruff --fix
- ruff-format
- black (language_version: python3.11)
- isort
```

## Import Organization

**Order:**
1. Standard library imports
2. Third-party imports (litellm, pytest, rich, etc.)
3. Local imports (relative with dots)

**Path Aliases:**
- Relative imports within packages: `from ..terminal_interface.local_setup import local_setup`
- Lazy imports to avoid circular dependencies:
```python
_AgentRole = None

def _get_agent_types():
    global _AgentRole
    if _AgentRole is None:
        from ...core.agents.types import AgentRole
        _AgentRole = AgentRole
    return _AgentRole
```

**Import Patterns:**
- `from typing import TYPE_CHECKING, Optional, Any`
- Guard type-only imports: `if TYPE_CHECKING: from ..core import OpenInterpreter`
- Module-level imports at top, lazy loading for heavy dependencies

## Error Handling

**Patterns:**
- Try/except with specific exception types
- Logging at appropriate levels (debug for non-critical, warning for recoverable)
- Return fallback values instead of re-raising when possible

```python
try:
    if os_module.path.exists(settings_path):
        with open(settings_path) as f:
            _settings_cache = json.load(f)
except json.JSONDecodeError as e:
    logging.getLogger(__name__).warning(
        f"Settings file {settings_path} is malformed: {e}. Using defaults."
    )
except OSError as e:
    logging.getLogger(__name__).debug(f"Could not load settings: {e}")
```

**Error Context:**
- Include file paths and variable values in error messages
- Use f-strings for error message formatting
- Return `None` or empty collections on soft failures

## Logging

**Framework:** Python logging module

**Setup Pattern:**
```python
import logging
logger = logging.getLogger(__name__)
```

**Levels:**
- `logger.debug()` for development/troubleshooting info
- `logger.info()` for informational messages (e.g., fallback to SQLite)
- `logger.warning()` for recoverable issues
- `logger.error()` for failures

**When to Log:**
- Lazy module loading fallbacks
- Configuration parsing issues
- Intent refiner failures (non-blocking)
- System message cache misses

## Comments

**When to Comment:**
- Module-level docstrings explaining purpose
- Class docstrings with usage examples
- Function docstrings for public APIs
- Inline comments for non-obvious logic (especially thread safety)

**Docstring Format:**
```python
"""
SemanticEditGraph - Persistent memory for code edits with semantic context.

This is the core component of the institutional memory system, providing:
- Storage of edit history with full context
- Querying edits by symbol, file, intent, or conversation
"""

def _detect_project_root(start_path: str) -> str:
    """
    Detect project root by walking upwards for common project markers.

    WHAT: Returns the nearest ancestor directory containing a known marker.
    WHY: Keeps Scout from treating the entire home directory as a codebase.

    Args:
        start_path: Starting path to search from (file or directory)

    Returns:
        Absolute project root path (directory).
    """
```

**Thread Safety Comments:**
```python
# NOTE: Thread safety analysis (2024-12):
# - _load_settings() and save_settings() are NOT locked with _module_lock
# - Race condition risk is LOW for CLI usage (single-threaded settings access)
# - For SDK/server with concurrent save_current_settings() calls, last-write-wins
```

## Function Design

**Size:** Functions are generally 20-50 lines. Complex functions are broken into helpers.

**Parameters:**
- Use keyword arguments for optional parameters
- Default to `None` for optional complex types, check in function body
- Use dataclasses for grouped parameters (e.g., `WorkflowResult`)

**Return Values:**
- Return dataclasses for complex results
- Return `None` on soft failure, raise on hard failure
- Use generators for streaming: `yield {"type": "message", "content": chunk}`

## Module Design

**Exports:**
- Use `__init__.py` to define public API
- Import submodule classes into package namespace
- Example from `interpreter/core/agents/__init__.py`:
```python
from .orchestrator import AgentOrchestrator
from .scout_agent import ScoutAgent
from .surgeon_agent import SurgeonAgent
```

**Barrel Files:**
- Each subpackage has `__init__.py` re-exporting key classes
- Avoids deep import paths for consumers

**Lazy Loading Pattern:**
Thread-safe double-checked locking for expensive modules:
```python
_module_lock = threading.Lock()
_memory_module = None

def _get_memory_module():
    global _memory_module
    if _memory_module is not None:
        return _memory_module
    with _module_lock:
        if _memory_module is None:
            from .memory import SemanticEditGraph
            _memory_module = {"SemanticEditGraph": SemanticEditGraph}
    return _memory_module
```

## Dataclass Conventions

**Pattern:**
```python
from dataclasses import dataclass, field

@dataclass(slots=True)
class WorkflowResult:
    """Result from a complete workflow."""
    workflow_type: WorkflowType
    success: bool = False
    agent_results: dict[AgentRole, AgentResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
```

**Features Used:**
- `slots=True` for memory efficiency
- `field(default_factory=...)` for mutable defaults
- Type hints on all fields

## Enum Conventions

**Pattern:**
```python
from enum import Enum, auto

class WorkflowType(Enum):
    NONE = "none"
    EXPLORE = "explore"
    EDIT = "edit"

class UIMode(Enum):
    ZEN = auto()
    STANDARD = auto()
    POWER = auto()
```

**String vs Auto:**
- Use string values when enum value needs to be serialized/displayed
- Use `auto()` for internal-only enums

## Environment Variables

**Naming:** Prefixed with `OI_` or `OPEN_INTERPRETER_`
- `OI_ACTIVATE_ALL`: Enable all features
- `OI_UI_DEBUG`: Debug logging
- `OI_NO_TUI`: Disable interactive mode
- `OI_ENABLE_UNSTEER`: Intent refinement
- `OPEN_INTERPRETER_APPROVAL`: Risk-based approval

**Access Pattern:**
```python
_OI_ACTIVATE_ALL = os_module.environ.get("OI_ACTIVATE_ALL", "").lower() in (
    "true", "1", "yes",
)
```

---

*Convention analysis: 2026-01-19*
