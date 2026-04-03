# Testing Patterns

**Analysis Date:** 2026-01-19

## Test Framework

**Runner:**
- pytest 7.4.0
- Config: `pyproject.toml` (no separate pytest.ini)

**Assertion Library:**
- pytest assertions (native `assert` statements)
- No additional assertion libraries

**Coverage:**
- pytest-cov ^4.1.0 available
- Not enforced in CI

**Run Commands:**
```bash
poetry run pytest -s -x           # Run all tests, stop on first failure
poetry run pytest tests/test_x.py::test_name  # Single test
poetry run pytest -v              # Verbose output
poetry run pytest --tb=short      # Short tracebacks
```

## Test File Organization

**Location:**
- Primary test directory: `tests/`
- Subdirectories mirror source structure:
  - `tests/core/` for `interpreter/core/` tests
  - `tests/core/agents/` for agent tests
  - `tests/core/validation/` for validation tests
  - `tests/core/computer/` for computer module tests

**Naming:**
- Test files: `test_*.py`
- Test classes: `Test*` (e.g., `TestWorkflowDetection`, `TestAgentState`)
- Test functions: `test_*` (e.g., `test_valid_python_syntax`)

**Structure:**
```
tests/
├── config.test.yaml
├── test_interpreter.py           # Main interpreter tests
├── test_terminal_ui_architecture.py  # UI component tests
├── test_sprint_enhancements.py   # Feature-specific tests
├── test_new_features.py          # Advanced feature tests
├── test_integration_hooks.py     # Plugin/hook tests
├── test_task_completion_fixes.py # Bug fix tests
└── core/
    ├── test_async_core.py
    ├── agents/
    │   ├── test_agent_system.py
    │   ├── test_agent_visualization.py
    │   ├── test_e2e_agent_invocation.py
    │   └── test_research_agent.py
    ├── validation/
    │   ├── test_syntax_checker.py
    │   └── test_validator.py
    └── computer/
        ├── documents/
        ├── files/
        └── search/
```

## Test Structure

**Suite Organization:**
```python
"""
Comprehensive tests for the Open Interpreter agent system.

Tests cover:
- Agent initialization and configuration
- Workflow detection logic
- Agent orchestration
- Individual agent execution
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestWorkflowDetection:
    """Test the _detect_workflow method."""

    def test_short_messages_return_none(self, orchestrator):
        """Short messages (<30 chars) should return NONE workflow."""
        from interpreter.core.agents.orchestrator import WorkflowType
        assert orchestrator._detect_workflow("hi") == WorkflowType.NONE

    def test_explore_workflow_detected(self, orchestrator):
        """Tasks with explore keywords and code context trigger EXPLORE."""
        # Test implementation
```

**Patterns:**
- Group related tests in classes
- Class-level docstrings describe test scope
- Function docstrings describe specific behavior being tested
- Descriptive test names: `test_validate_syntax_error`, `test_cache_prevents_duplicate_calls`

## Fixtures

**Framework:** pytest fixtures

**Common Patterns:**

**Temporary Project Fixture:**
```python
@pytest.fixture
def temp_project():
    """Create a temporary project structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)

        (project / "main.py").write_text('''
def main():
    print("Hello")
''')
        (project / "lib").mkdir()
        (project / "lib" / "__init__.py").write_text("")

        yield str(project)
```

**Mock Interpreter Fixture:**
```python
@pytest.fixture
def mock_interpreter():
    """Create a mock interpreter for testing."""
    mock = MagicMock()
    mock.enable_agents = True
    mock.auto_run = False
    mock.semantic_graph = None
    mock.messages = []
    mock.llm = MagicMock()
    mock.llm.run = _create_llm_run_mock()
    return mock
```

**Validator Fixture:**
```python
@pytest.fixture
def validator(self):
    return EditValidator(run_tests=False, run_type_check=False)
```

**Chained Fixtures:**
```python
@pytest.fixture
def orchestrator(mock_interpreter, temp_project):
    """Create an AgentOrchestrator instance."""
    from interpreter.core.agents.orchestrator import AgentOrchestrator
    return AgentOrchestrator(
        interpreter=mock_interpreter,
        root_path=temp_project,
        event_bus=None,
    )
```

## Mocking

**Framework:** `unittest.mock` (MagicMock, patch)

**Patterns:**

**Mock LLM Responses:**
```python
def _create_llm_run_mock():
    """Create a mock for llm.run() that returns workflow type."""
    def mock_run(messages):
        prompt = messages[0].get("content", "") if messages else ""
        if "find" in prompt.lower():
            response = "EXPLORE"
        else:
            response = "NONE"
        yield {"type": "message", "content": response}
    return mock_run
```

**Patching:**
```python
with patch.object(
    orchestrator.get_agent.__self__.__class__,
    "_create_agent",
) as mock_create:
    mock_agent = MagicMock()
    mock_agent.run.side_effect = RuntimeError("Test error")
    mock_create.return_value = mock_agent

    result = orchestrator.handle_task("find files", workflow=WorkflowType.EXPLORE)
    assert not result.success
```

**Event Bus Mocking:**
```python
mock_bus = MagicMock()
mock_bus.emit = MagicMock()

orchestrator = AgentOrchestrator(
    interpreter=mock_interpreter,
    event_bus=mock_bus,
)

orchestrator.handle_task("find all Python files")
assert mock_bus.emit.called
```

**What to Mock:**
- LLM calls (expensive, external)
- Event bus (verify events emitted)
- File system operations (use tempfile instead when possible)
- External services (API calls)

**What NOT to Mock:**
- Core logic being tested
- Simple utility functions
- Dataclass creation

## Fixtures and Factories

**Test Data:**
```python
# Inline test data
content = '''
def hello():
    return "Hello, World!"
'''
result = validator.validate_syntax_only("test.py", content)
```

**File Creation in Tests:**
```python
with tempfile.TemporaryDirectory() as tmpdir:
    src = os.path.join(tmpdir, "module1.py")
    with open(src, "w") as f:
        f.write("def func1(): pass")

    test = os.path.join(tmpdir, "test_module1.py")
    with open(test, "w") as f:
        f.write("from module1 import func1\ndef test_func1(): pass")
```

**Location:**
- Fixtures defined in test files near usage
- No `conftest.py` for shared fixtures currently
- Test data created inline or in fixtures

## Coverage

**Requirements:** Not enforced (no minimum coverage target)

**View Coverage:**
```bash
poetry run pytest --cov=interpreter --cov-report=html
```

## Test Types

**Unit Tests:**
- Scope: Individual functions and methods
- Example: `test_valid_python_syntax`, `test_context_usage_percent`
- Pattern: Isolated, fast, no external dependencies

**Integration Tests:**
- Scope: Multiple components working together
- Example: `TestOrchestratorIntegration`, `TestRespondIntegration`
- Pattern: Uses fixtures to set up component chains

**End-to-End Tests:**
- Scope: Full system flow
- Example: `test_authenticated_acknowledging_breaking_server`
- Often skipped with `@pytest.mark.skip(reason="...")`

**Server Tests:**
- Use `multiprocessing.Process` to spawn server
- WebSocket testing with `websockets` library
- Async test functions with `asyncio`

## Skip Patterns

**Conditional Skips:**
```python
@pytest.mark.skipif(not FASTAPI_AVAILABLE, reason="FastAPI not installed")
def test_server():
    ...
```

**Platform Skips:**
```python
@pytest.mark.skip(reason="Mac only")
def test_sms():
    ...
```

**Feature Skips:**
```python
@pytest.mark.skip(reason="Requires open-interpreter[local]")
def test_localos():
    ...

@pytest.mark.skip(reason="Computer with display only + no way to fail test")
def test_display_api():
    ...
```

## Common Patterns

**Setup/Teardown:**
```python
def setup_function():
    """Run before each test."""
    interpreter.reset()
    interpreter.llm.temperature = 0
    interpreter.auto_run = True
    interpreter.llm.model = "gpt-4o-mini"

def teardown_function():
    """Run after each test."""
    time.sleep(4)  # API rate limiting
```

**Class-based Setup:**
```python
class TestEventBus:
    def setup_method(self):
        """Reset event bus before each test."""
        reset_event_bus()
```

**Async Testing:**
```python
async def test_fastapi_server():
    async with websockets.connect("ws://localhost:8123/") as websocket:
        await websocket.send(json.dumps({"auth": "testing"}))
        message = await websocket.recv()
        assert "crunk" in accumulated_content

loop = asyncio.get_event_loop()
loop.run_until_complete(test_fastapi_server())
```

**Error Testing:**
```python
def test_invalid_agent_role_raises(self, orchestrator):
    """Requesting an unimplemented agent should raise."""
    with pytest.raises(ValueError, match="No agent implementation"):
        orchestrator.get_agent(AgentRole.HISTORIAN)
```

**Assertion Patterns:**
```python
# Simple assertions
assert result.valid
assert len(result.errors) == 0
assert "test_utils" in str(t) for t in tests

# Collection assertions
assert len(files) >= 3
assert any("utils.py" in r.file_path for r in results)

# Dictionary assertions
assert "file1.py" in result
assert task_hash in orch._workflow_cache

# Message assertions (with context)
assert len(task) >= 30, f"Task too short: {task}"
assert result == WorkflowType.EXPLORE, f"Failed for: {task}"
```

## Test Categories (by file)

**`test_interpreter.py`:** Core interpreter functionality
- Hallucination resilience
- Server tests (authenticated, basic)
- Generator streaming
- File handling
- Token counting

**`test_terminal_ui_architecture.py`:** UI component tests
- UIState management
- Event bus functionality
- Backend creation
- Sanitizer tests
- Input handling

**`test_sprint_enhancements.py`:** Feature sprint tests
- Spinner sleep
- Context meter warnings
- Timeout handling
- Agent fallback events
- Database indexes
- Model switching lock
- Batch test discovery
- Lazy MCP connection
- Workflow cache

**`test_agent_system.py`:** Agent orchestration tests
- Workflow detection
- Agent initialization
- Scout/Surgeon/Architect/Validator agents
- Error handling
- Event emission

**`test_validator.py`:** Validation tests
- Syntax validation
- Edit validation
- Sandbox validation
- Configuration options

---

*Testing analysis: 2026-01-19*
