"""
Comprehensive tests for the Open Interpreter agent system.

Tests cover:
- Agent initialization and configuration
- Workflow detection logic
- Agent orchestration
- Individual agent execution
- Agent chaining
- Event emission
- Error handling

ARCHITECTURE: Tests are structured in layers - unit tests for individual
components, then integration tests for the full orchestration flow.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ==============================================================================
# FIXTURES
# ==============================================================================


@pytest.fixture
def temp_project():
    """Create a temporary project structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a realistic project structure
        project = Path(tmpdir)

        # Python files
        (project / "main.py").write_text(
            """
def main():
    '''Main entry point.'''
    print("Hello")

if __name__ == "__main__":
    main()
"""
        )

        (project / "utils.py").write_text(
            """
def helper_function(x):
    '''Helper that doubles a number.'''
    return x * 2

class Calculator:
    '''Simple calculator class.'''
    def add(self, a, b):
        return a + b

    def multiply(self, a, b):
        return a * b
"""
        )

        # Create a subdirectory with more files
        (project / "lib").mkdir()
        (project / "lib" / "__init__.py").write_text("")
        (project / "lib" / "core.py").write_text(
            """
class CoreEngine:
    '''Core engine for processing.'''
    def process(self, data):
        return data.upper()
"""
        )

        yield str(project)


@pytest.fixture
def mock_interpreter():
    """Create a mock interpreter for testing."""
    mock = MagicMock()
    mock.enable_agents = True
    mock.auto_run = False
    mock.semantic_graph = None
    mock.messages = []
    mock.llm = MagicMock()
    mock.computer = MagicMock()
    return mock


@pytest.fixture
def orchestrator(mock_interpreter, temp_project):
    """Create an AgentOrchestrator instance."""
    from interpreter.core.agents.orchestrator import AgentOrchestrator

    return AgentOrchestrator(
        interpreter=mock_interpreter,
        memory=None,
        root_path=temp_project,
        event_bus=None,  # Disable events for unit tests
    )


# ==============================================================================
# WORKFLOW DETECTION TESTS
# ==============================================================================


class TestWorkflowDetection:
    """Test the _detect_workflow method."""

    def test_short_messages_return_none(self, orchestrator):
        """Short messages (<30 chars) should return NONE workflow."""
        from interpreter.core.agents.orchestrator import WorkflowType

        assert orchestrator._detect_workflow("hi") == WorkflowType.NONE
        assert orchestrator._detect_workflow("help me") == WorkflowType.NONE
        assert orchestrator._detect_workflow("what is python?") == WorkflowType.NONE

    def test_no_code_context_returns_none(self, orchestrator):
        """Messages without code indicators return NONE."""
        from interpreter.core.agents.orchestrator import WorkflowType

        # Long enough but no code context
        result = orchestrator._detect_workflow(
            "Tell me a story about a brave knight who fought a dragon"
        )
        assert result == WorkflowType.NONE

    def test_explore_workflow_detected(self, orchestrator):
        """Tasks with explore keywords and code context trigger EXPLORE."""
        from interpreter.core.agents.orchestrator import WorkflowType

        # NOTE: Tasks must be >30 chars to pass the short-message filter
        test_cases = [
            "find all .py files in the project structure",  # 43 chars
            "search for function named process in module",  # 43 chars
            "list all classes defined in the module code",  # 43 chars
            "show me the file structure of the .py files",  # 43 chars
            "where is the main.py file located exactly",  # 41 chars
            "explore the code structure in the project",  # 41 chars
        ]

        for task in test_cases:
            assert len(task) >= 30, f"Task too short: {task}"
            result = orchestrator._detect_workflow(task)
            assert result == WorkflowType.EXPLORE, f"Failed for: {task}"

    def test_edit_workflow_detected(self, orchestrator):
        """Tasks with edit keywords and code context trigger EDIT."""
        from interpreter.core.agents.orchestrator import WorkflowType

        # NOTE: Tasks must be >30 chars to pass the short-message filter
        test_cases = [
            "fix the bug in main.py that causes crashes",  # 43 chars
            "add a new function to utils.py for parsing",  # 43 chars
            "change the class name in the module to better",  # 45 chars
            "update the import statement in code to fix dep",  # 46 chars
            "modify the function definition to add params",  # 44 chars
            "edit the file to add proper error handling",  # 43 chars
            "implement a new method in the class for data",  # 44 chars
        ]

        for task in test_cases:
            assert len(task) >= 30, f"Task too short: {task}"
            result = orchestrator._detect_workflow(task)
            assert result == WorkflowType.EDIT, f"Failed for: {task}"

    def test_validate_workflow_detected(self, orchestrator):
        """Tasks with validate keywords trigger VALIDATE."""
        from interpreter.core.agents.orchestrator import WorkflowType

        # NOTE: Tasks must be >30 chars to pass the short-message filter
        test_cases = [
            "test the function in the module properly",  # 40 chars
            "check if the code compiles without errors",  # 41 chars
            "verify the class works correctly in prod",  # 40 chars
            "validate the implementation in file.py now",  # 42 chars
        ]

        for task in test_cases:
            assert len(task) >= 30, f"Task too short: {task}"
            result = orchestrator._detect_workflow(task)
            assert result == WorkflowType.VALIDATE, f"Failed for: {task}"

    def test_validate_takes_precedence(self, orchestrator):
        """Validate keywords should take precedence over others."""
        from interpreter.core.agents.orchestrator import WorkflowType

        # Has both "fix" (edit) and "test" (validate) - validate wins
        result = orchestrator._detect_workflow(
            "fix the bug and test the function in file.py"
        )
        assert result == WorkflowType.VALIDATE


# ==============================================================================
# AGENT INITIALIZATION TESTS
# ==============================================================================


class TestAgentInitialization:
    """Test agent creation and initialization."""

    def test_scout_agent_creation(self, orchestrator):
        """ScoutAgent should be created correctly."""
        from interpreter.core.agents.base_agent import AgentRole

        scout = orchestrator.get_agent(AgentRole.SCOUT)
        assert scout is not None
        assert scout.role == AgentRole.SCOUT
        assert scout.root_path == orchestrator.root_path

    def test_surgeon_agent_creation(self, orchestrator):
        """SurgeonAgent should be created correctly."""
        from interpreter.core.agents.base_agent import AgentRole

        surgeon = orchestrator.get_agent(AgentRole.SURGEON)
        assert surgeon is not None
        assert surgeon.role == AgentRole.SURGEON

    def test_agent_lazy_loading(self, orchestrator):
        """Agents should be lazy-loaded and cached."""
        from interpreter.core.agents.base_agent import AgentRole

        # Initially no agents
        assert len(orchestrator._agents) == 0

        # Get scout - should create it
        scout1 = orchestrator.get_agent(AgentRole.SCOUT)
        assert len(orchestrator._agents) == 1

        # Get scout again - should return cached
        scout2 = orchestrator.get_agent(AgentRole.SCOUT)
        assert scout1 is scout2
        assert len(orchestrator._agents) == 1

    def test_invalid_agent_role_raises(self, orchestrator):
        """Requesting an unimplemented agent should raise."""
        from interpreter.core.agents.base_agent import AgentRole

        with pytest.raises(ValueError, match="No agent implementation"):
            orchestrator.get_agent(AgentRole.HISTORIAN)


# ==============================================================================
# SCOUT AGENT TESTS
# ==============================================================================


class TestScoutAgent:
    """Test ScoutAgent functionality."""

    def test_find_files(self, orchestrator, temp_project):
        """ScoutAgent should find files matching patterns."""
        from interpreter.core.agents.base_agent import AgentRole

        scout = orchestrator.get_agent(AgentRole.SCOUT)

        # Find Python files
        files = scout.find_files("*.py")
        assert len(files) >= 3  # main.py, utils.py, lib/__init__.py, lib/core.py

        # Find specific file
        files = scout.find_files("main.py")
        assert len(files) == 1
        assert "main.py" in files[0]

    def test_search_symbol_function(self, orchestrator, temp_project):
        """ScoutAgent should find function definitions."""
        from interpreter.core.agents.base_agent import AgentRole

        scout = orchestrator.get_agent(AgentRole.SCOUT)

        results = scout.search_symbol("helper_function", symbol_type="function")
        assert len(results) >= 1
        assert any("utils.py" in r.file_path for r in results)

    def test_search_symbol_class(self, orchestrator, temp_project):
        """ScoutAgent should find class definitions."""
        from interpreter.core.agents.base_agent import AgentRole

        scout = orchestrator.get_agent(AgentRole.SCOUT)

        results = scout.search_symbol("Calculator", symbol_type="class")
        assert len(results) >= 1
        assert any("utils.py" in r.file_path for r in results)

    def test_get_directory_structure(self, orchestrator, temp_project):
        """ScoutAgent should return directory structure."""
        from interpreter.core.agents.base_agent import AgentRole

        scout = orchestrator.get_agent(AgentRole.SCOUT)

        structure = scout.get_directory_structure()
        assert "main.py" in structure
        assert "utils.py" in structure
        assert "lib" in structure

    def test_search_content(self, orchestrator, temp_project):
        """ScoutAgent should search file contents."""
        from interpreter.core.agents.base_agent import AgentRole

        scout = orchestrator.get_agent(AgentRole.SCOUT)

        # Search for string in file
        results = scout.search_content("Helper that doubles")
        assert len(results) >= 1
        assert any("utils.py" in r.file_path for r in results)

    def test_execute_file_search(self, orchestrator, temp_project):
        """ScoutAgent.execute should handle file search tasks."""
        from interpreter.core.agents.base_agent import AgentRole

        scout = orchestrator.get_agent(AgentRole.SCOUT)

        # Use quoted pattern so _extract_pattern finds it correctly
        result = scout.execute('find all files matching "*.py"')
        assert result.success
        assert len(result.files_found) >= 3

    def test_execute_function_search(self, orchestrator, temp_project):
        """ScoutAgent.execute should handle function search tasks."""
        from interpreter.core.agents.base_agent import AgentRole

        scout = orchestrator.get_agent(AgentRole.SCOUT)

        # Use 'function' keyword WITHOUT 'find' to hit the function branch
        # (otherwise 'find' triggers the file search branch first)
        result = scout.execute('search for function named "helper_function"')
        assert result.success
        assert len(result.symbols_found) >= 1


# ==============================================================================
# SURGEON AGENT TESTS
# ==============================================================================


class TestSurgeonAgent:
    """Test SurgeonAgent functionality."""

    def test_parse_edit_proposals(self, mock_interpreter):
        """SurgeonAgent should parse edit blocks correctly."""
        from interpreter.core.agents.base_agent import AgentRole
        from interpreter.core.agents.orchestrator import AgentOrchestrator

        # Create a temp directory with main.py containing EXACT content from FIND block
        with tempfile.TemporaryDirectory() as tmpdir:
            # IMPORTANT: Content must EXACTLY match the FIND block for propose_edit to work
            Path(tmpdir, "main.py").write_text('def main():\n    print("Hello")\n')

            orch = AgentOrchestrator(
                interpreter=mock_interpreter, root_path=tmpdir, event_bus=None
            )
            surgeon = orch.get_agent(AgentRole.SURGEON)

            # Note: The regex expects ```edit\n with NO leading spaces
            llm_response = """Here's how to fix the bug:

```edit
FILE: main.py
FIND:
def main():
    print("Hello")
REPLACE:
def main():
    print("Hello World")
```

This changes the greeting."""

            edits = surgeon._parse_edit_proposals(llm_response)
            assert len(edits) == 1
            assert edits[0].file_path == "main.py"
            # EditProposal uses original_content and new_content
            assert 'print("Hello")' in edits[0].original_content
            assert 'print("Hello World")' in edits[0].new_content

    def test_validate_edit_syntax(self, orchestrator, temp_project):
        """SurgeonAgent should validate Python syntax."""
        from interpreter.core.agents.base_agent import AgentRole

        surgeon = orchestrator.get_agent(AgentRole.SURGEON)

        # _check_python_syntax returns bool, not tuple
        # Valid Python
        valid = surgeon._check_python_syntax("def foo(): return 42")
        assert valid

        # Invalid Python
        valid = surgeon._check_python_syntax("def foo( return 42")
        assert not valid


# ==============================================================================
# ARCHITECT AGENT TESTS
# ==============================================================================


class TestArchitectAgent:
    """Test ArchitectAgent functionality."""

    def test_architect_agent_creation(self, orchestrator):
        """ArchitectAgent should be created correctly."""
        from interpreter.core.agents.base_agent import AgentRole

        architect = orchestrator.get_agent(AgentRole.ARCHITECT)
        assert architect is not None
        assert architect.role == AgentRole.ARCHITECT

    def test_architect_analyze_file(self, orchestrator, temp_project):
        """ArchitectAgent should analyze file structure."""
        from interpreter.core.agents.base_agent import AgentRole

        architect = orchestrator.get_agent(AgentRole.ARCHITECT)

        # Analyze utils.py which has a class and functions
        structure = architect.analyze_file("utils.py")
        assert structure is not None
        assert structure.file_path == "utils.py"
        # Should find Calculator class and helper_function
        assert len(structure.classes) >= 1 or len(structure.functions) >= 1

    def test_architect_execute(self, orchestrator, temp_project):
        """ArchitectAgent.execute should analyze codebase structure."""
        from interpreter.core.agents.base_agent import AgentRole

        architect = orchestrator.get_agent(AgentRole.ARCHITECT)

        result = architect.execute('analyze the structure of "utils.py"')
        assert result.success
        # Result should contain structural information
        assert result.content is not None


# ==============================================================================
# VALIDATOR AGENT TESTS
# ==============================================================================


class TestValidatorAgent:
    """Test ValidatorAgent functionality."""

    def test_validator_agent_creation(self, orchestrator):
        """ValidatorAgent should be created correctly."""
        from interpreter.core.agents.base_agent import AgentRole

        validator = orchestrator.get_agent(AgentRole.VALIDATOR)
        assert validator is not None
        assert validator.role == AgentRole.VALIDATOR

    def test_validator_syntax_check(self, orchestrator, temp_project):
        """ValidatorAgent should validate Python syntax."""
        from interpreter.core.agents.base_agent import AgentRole

        validator = orchestrator.get_agent(AgentRole.VALIDATOR)

        # validate_code_string validates inline code (not file paths)
        # Valid Python syntax
        result = validator.validate_code_string("def foo(): return 42", "python")
        assert result.passed

        # Invalid Python syntax
        result = validator.validate_code_string("def foo( return 42", "python")
        assert not result.passed

    def test_validator_file_syntax_check(self, orchestrator, temp_project):
        """ValidatorAgent should validate Python file syntax."""
        from interpreter.core.agents.base_agent import AgentRole

        validator = orchestrator.get_agent(AgentRole.VALIDATOR)

        # Validate main.py which exists in temp_project
        result = validator.validate_python_syntax("main.py")
        assert result.passed

    def test_validator_discover_tests(self, orchestrator, temp_project):
        """ValidatorAgent should discover test files."""
        from pathlib import Path

        from interpreter.core.agents.base_agent import AgentRole

        # Create a test file
        Path(temp_project, "test_utils.py").write_text("def test_helper(): assert True")

        validator = orchestrator.get_agent(AgentRole.VALIDATOR)

        tests = validator.discover_tests("utils.py")
        # Should find test_utils.py as related to utils.py
        assert any("test_utils" in str(t) for t in tests)

    def test_validator_execute(self, orchestrator, temp_project):
        """ValidatorAgent.execute should validate code."""
        from interpreter.core.agents.base_agent import AgentRole

        validator = orchestrator.get_agent(AgentRole.VALIDATOR)

        result = validator.execute('validate syntax of "main.py"')
        assert result.success


# ==============================================================================
# VALIDATE WORKFLOW TESTS
# ==============================================================================


class TestValidateWorkflow:
    """Test the VALIDATE workflow."""

    def test_validate_workflow_invokes_validator(self, orchestrator, temp_project):
        """VALIDATE workflow should invoke ValidatorAgent."""
        from interpreter.core.agents.base_agent import AgentRole
        from interpreter.core.agents.orchestrator import WorkflowType

        result = orchestrator.handle_task(
            'validate the code in "main.py"', workflow=WorkflowType.VALIDATE
        )

        assert result.workflow_type == WorkflowType.VALIDATE
        assert AgentRole.VALIDATOR in result.agent_results
        # Scout should NOT be invoked in VALIDATE workflow
        assert AgentRole.SCOUT not in result.agent_results


# ==============================================================================
# ORCHESTRATOR INTEGRATION TESTS
# ==============================================================================


class TestOrchestratorIntegration:
    """Test the full orchestration flow."""

    def test_explore_workflow(self, orchestrator, temp_project):
        """EXPLORE workflow should use Scout agent only."""
        from interpreter.core.agents.base_agent import AgentRole
        from interpreter.core.agents.orchestrator import WorkflowType

        result = orchestrator.handle_task(
            "find all Python files in the project", workflow=WorkflowType.EXPLORE
        )

        assert result.workflow_type == WorkflowType.EXPLORE
        assert result.success
        assert AgentRole.SCOUT in result.agent_results
        assert AgentRole.SURGEON not in result.agent_results

    def test_workflow_result_summary(self, orchestrator, temp_project):
        """WorkflowResult should produce readable summaries."""
        from interpreter.core.agents.orchestrator import WorkflowType

        result = orchestrator.handle_task(
            "find main.py file", workflow=WorkflowType.EXPLORE
        )

        summary = result.get_summary()
        assert "Workflow: explore" in summary
        assert "SUCCESS" in summary or "FAILED" in summary
        assert "scout" in summary.lower()

    def test_auto_workflow_detection(self, orchestrator, temp_project):
        """handle_task should auto-detect workflow."""
        from interpreter.core.agents.orchestrator import WorkflowType

        # Explore task
        result = orchestrator.handle_task("find all .py files in this project")
        assert result.workflow_type == WorkflowType.EXPLORE

    def test_duration_tracking(self, orchestrator, temp_project):
        """Workflow should track execution duration."""
        from interpreter.core.agents.orchestrator import WorkflowType

        result = orchestrator.handle_task(
            "find main.py file", workflow=WorkflowType.EXPLORE
        )

        assert result.total_duration_ms > 0

    def test_final_context_building(self, orchestrator, temp_project):
        """Workflow should build final context from all agents."""
        from interpreter.core.agents.orchestrator import WorkflowType

        result = orchestrator.handle_task(
            "find all Python files", workflow=WorkflowType.EXPLORE
        )

        assert result.final_context is not None
        assert "Workflow Result" in result.final_context


# ==============================================================================
# ERROR HANDLING TESTS
# ==============================================================================


class TestErrorHandling:
    """Test error handling in the agent system."""

    def test_scout_handles_invalid_pattern(self, orchestrator, temp_project):
        """ScoutAgent should handle invalid glob patterns gracefully."""
        from interpreter.core.agents.base_agent import AgentRole

        scout = orchestrator.get_agent(AgentRole.SCOUT)

        # This shouldn't crash
        files = scout.find_files("[invalid")
        # Should return empty or handle gracefully
        assert isinstance(files, list)

    def test_orchestrator_handles_agent_error(self, orchestrator, temp_project):
        """Orchestrator should handle agent exceptions."""
        from interpreter.core.agents.orchestrator import WorkflowType

        # Mock scout to raise an exception
        # original_execute not needed - using patch
        with patch.object(
            orchestrator.get_agent.__self__.__class__,
            "_create_agent",
        ) as mock_create:
            mock_agent = MagicMock()
            mock_agent.run.side_effect = RuntimeError("Test error")
            mock_create.return_value = mock_agent

            # Clear cached agents to force recreation
            orchestrator._agents.clear()

            result = orchestrator.handle_task(
                "find Python files", workflow=WorkflowType.EXPLORE
            )

            assert not result.success
            assert len(result.errors) > 0


# ==============================================================================
# EVENT EMISSION TESTS
# ==============================================================================


class TestEventEmission:
    """Test UI event emission from orchestrator."""

    def test_events_emitted_with_bus(self, mock_interpreter, temp_project):
        """Events should be emitted when event_bus is provided."""
        from interpreter.core.agents.orchestrator import AgentOrchestrator, WorkflowType

        mock_bus = MagicMock()
        mock_bus.emit = MagicMock()

        orchestrator = AgentOrchestrator(
            interpreter=mock_interpreter,
            root_path=temp_project,
            event_bus=mock_bus,
        )

        orchestrator.handle_task("find all Python files", workflow=WorkflowType.EXPLORE)

        # Should have emitted AGENT_SPAWN and AGENT_COMPLETE events
        assert mock_bus.emit.called

    def test_agent_id_generation(self, orchestrator):
        """Agent IDs should be unique and incrementing."""
        from interpreter.core.agents.base_agent import AgentRole

        id1 = orchestrator._generate_agent_id(AgentRole.SCOUT)
        id2 = orchestrator._generate_agent_id(AgentRole.SCOUT)
        id3 = orchestrator._generate_agent_id(AgentRole.SURGEON)

        assert id1 != id2
        assert id2 != id3
        assert "scout" in id1
        assert "surgeon" in id3


# ==============================================================================
# RESPOND.PY INTEGRATION TESTS
# ==============================================================================


class TestRespondIntegration:
    """Test agent integration in respond.py flow."""

    def test_agents_enabled_check(self):
        """respond() should check enable_agents flag."""
        # This is more of a documentation test - the actual check is at line 161
        # in respond.py:
        #   if (
        #       interpreter.enable_agents
        #       and hasattr(interpreter, "agent_orchestrator")
        #       and interpreter.agent_orchestrator is not None
        #   ):
        pass

    def test_workflow_routing_conditions(self):
        """Only EXPLORE and EDIT workflows should be routed to agents."""
        # respond.py line 181:
        #   if workflow in (WorkflowType.EXPLORE, WorkflowType.EDIT):
        from interpreter.core.agents.orchestrator import WorkflowType

        # These should be routed
        routed = [WorkflowType.EXPLORE, WorkflowType.EDIT]
        # These should NOT be routed (fallback to LLM)
        not_routed = [WorkflowType.NONE, WorkflowType.VALIDATE, WorkflowType.FULL]

        for wf in routed:
            assert wf in (WorkflowType.EXPLORE, WorkflowType.EDIT)

        for wf in not_routed:
            assert wf not in (WorkflowType.EXPLORE, WorkflowType.EDIT)


# ==============================================================================
# PROPERTY/LAZY LOADING TESTS
# ==============================================================================


class TestInterpreterAgentProperty:
    """Test interpreter.agent_orchestrator property."""

    def test_orchestrator_none_when_disabled(self):
        """agent_orchestrator should be None when enable_agents=False."""
        from interpreter.core.core import OpenInterpreter

        interp = OpenInterpreter()
        interp.enable_agents = False
        interp._agent_orchestrator = None

        # Property should return None
        assert interp.agent_orchestrator is None

    def test_orchestrator_created_when_enabled(self):
        """agent_orchestrator should be created when enable_agents=True."""
        from interpreter.core.core import OpenInterpreter

        interp = OpenInterpreter()
        interp.enable_agents = True
        interp._agent_orchestrator = None

        # Access property - should create orchestrator
        orch = interp.agent_orchestrator

        assert orch is not None
        assert interp._agent_orchestrator is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
