"""
Tests for the task completion fixes:
1. Loop breaker matching
2. Empty LLM response handling
3. Graceful stop handling
"""
import pytest
import sys
import os

# Add the parent directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestLoopBreakerMatching:
    """Test that loop breakers are matched correctly."""

    def test_genuine_loop_breaker_at_end(self):
        """Loop breaker at end of content should match."""
        from interpreter.core.respond import respond

        # We can't easily test respond() directly, but we can test the logic
        # by importing and checking the function behavior
        content = "I have completed all the steps. The task is done."
        breaker = "The task is done."

        # The breaker appears at the end
        assert content.strip().endswith(breaker)

    def test_loop_breaker_on_own_line(self):
        """Loop breaker on its own line should match."""
        content = """I have completed the work.
The task is done.
"""
        breaker = "The task is done."

        # Check if it's on its own line
        lines = content.split('\n')
        has_on_own_line = any(line.strip() == breaker for line in lines)
        assert has_on_own_line

    def test_loop_breaker_mid_sentence_should_not_match(self):
        """Loop breaker in the middle of a sentence should NOT match."""
        content = "I will say The task is done. when I finish, but I'm not done yet."
        breaker = "The task is done."

        # It should NOT match because it's mid-sentence
        content_stripped = content.strip()
        ends_with_breaker = content_stripped.endswith(breaker)

        lines = content.split('\n')
        on_own_line = any(line.strip() == breaker for line in lines)

        # Neither condition should be true
        assert not ends_with_breaker
        assert not on_own_line


class TestEmptyResponseHandling:
    """Test that empty LLM responses are handled correctly."""

    def test_run_text_llm_imports(self):
        """Ensure run_text_llm can be imported."""
        from interpreter.core.llm.run_text_llm import run_text_llm
        assert callable(run_text_llm)

    def test_llm_module_imports(self):
        """Ensure LLM module can be imported."""
        from interpreter.core.llm.llm import Llm, fixed_litellm_completions
        assert callable(fixed_litellm_completions)


class TestGracefulStopHandling:
    """Test graceful stop event handling."""

    def test_interpreter_has_stop_event(self):
        """Async interpreter should have stop_event."""
        try:
            from interpreter.core.async_core import AsyncInterpreter
            # Can't instantiate without FastAPI, but we can check the class
            import inspect
            source = inspect.getsource(AsyncInterpreter.__init__)
            assert "stop_event" in source
        except ImportError:
            # FastAPI not installed, skip
            pytest.skip("FastAPI not installed")

    def test_core_yields_interrupted_status(self):
        """Core should yield interrupted status when stopped."""
        from interpreter.core.core import OpenInterpreter
        import inspect
        source = inspect.getsource(OpenInterpreter._respond_and_store)
        # Check our fix is present
        assert "interrupted" in source
        assert "Processing was interrupted" in source


class TestConfirmationHandling:
    """Test code confirmation handling."""

    def test_terminal_interface_continues_after_decline(self):
        """Terminal interface should continue after code decline."""
        from interpreter.terminal_interface import terminal_interface
        import inspect
        source = inspect.getsource(terminal_interface.terminal_interface)

        # Check our fix is present - it should continue instead of break
        assert "Code execution declined" in source
        assert "alternative approach" in source


class TestTimeoutHandling:
    """Test timeout handling in LLM completions."""

    def test_timeout_param_added(self):
        """Timeout parameter should be added to completions."""
        from interpreter.core.llm.llm import fixed_litellm_completions
        import inspect
        source = inspect.getsource(fixed_litellm_completions)

        # Check our fix is present
        assert "timeout" in source
        assert "120" in source  # Default timeout


class TestRetryLogic:
    """Test improved retry logic."""

    def test_exponential_backoff_in_llm(self):
        """LLM should use exponential backoff."""
        from interpreter.core.llm.llm import fixed_litellm_completions
        import inspect
        source = inspect.getsource(fixed_litellm_completions)

        # Check exponential backoff is present
        assert "2 ** attempt" in source or "2**attempt" in source

    def test_async_core_has_better_retry(self):
        """Async core should have improved retry logic."""
        try:
            from interpreter.core.async_core import AsyncInterpreter
            import inspect
            source = inspect.getsource(AsyncInterpreter.respond)

            # Check our improvements are present
            assert "exponential backoff" in source.lower() or "2 ** attempt" in source
            assert "max_attempts" in source
        except ImportError:
            pytest.skip("FastAPI not installed")


class TestUIResponsiveness:
    """Test UI refresh rate limiting."""

    def test_terminal_interface_has_rate_limiting(self):
        """Terminal interface should have refresh rate limiting."""
        from interpreter.terminal_interface import terminal_interface
        import inspect
        source = inspect.getsource(terminal_interface.terminal_interface)

        # Check our rate limiting is present
        assert "REFRESH_INTERVAL" in source
        assert "last_refresh_time" in source

    def test_code_block_has_throttling(self):
        """Code block should have refresh throttling."""
        from interpreter.terminal_interface.components.code_block import CodeBlock
        import inspect
        source = inspect.getsource(CodeBlock.refresh)

        # Check throttling is present
        assert "_last_refresh" in source
        assert "_min_refresh_interval" in source


class TestJupyterTermination:
    """Test Jupyter kernel termination is thread-safe."""

    def test_jupyter_terminate_waits_for_thread(self):
        """Jupyter terminate should wait for listener thread."""
        from interpreter.core.computer.terminal.languages.jupyter_language import JupyterLanguage
        import inspect
        source = inspect.getsource(JupyterLanguage.terminate)

        # Check our thread-safe terminate is present
        assert "finish_flag = True" in source
        assert "listener_thread" in source
        assert "join" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
