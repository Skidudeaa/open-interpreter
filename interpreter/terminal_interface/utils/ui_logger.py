"""
UI Logger - Debug logging for terminal interface components.

Replaces silent exception handling with proper logging.
Enable debug logging via OI_UI_DEBUG=true environment variable.
"""

import logging
import os
import sys
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

# Logger configuration
UI_DEBUG = os.environ.get("OI_UI_DEBUG", "").lower() in ("true", "1", "yes")


def get_log_path() -> Path:
    """Get path for UI debug log file."""
    log_dir = Path.home() / ".open-interpreter" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "ui_debug.log"


def setup_ui_logger() -> logging.Logger:
    """Set up the UI debug logger."""
    logger = logging.getLogger("oi.ui")

    if UI_DEBUG:
        logger.setLevel(logging.DEBUG)

        # File handler
        file_handler = logging.FileHandler(get_log_path())
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        ))
        logger.addHandler(file_handler)

        # Console handler for errors only
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.ERROR)
        console_handler.setFormatter(logging.Formatter(
            "[UI Error] %(message)s"
        ))
        logger.addHandler(console_handler)

    else:
        # Silent mode - only log to file if it exists
        logger.setLevel(logging.WARNING)
        null_handler = logging.NullHandler()
        logger.addHandler(null_handler)

    return logger


# Global logger instance
ui_logger = setup_ui_logger()


def log_exception(component: str, operation: str, error: Exception):
    """
    Log a UI exception with context.

    Args:
        component: Component name (e.g., "StatusBar", "CodeBlock")
        operation: What was being attempted
        error: The exception that occurred
    """
    ui_logger.debug(
        f"[{component}] {operation} failed: {type(error).__name__}: {error}",
        exc_info=UI_DEBUG
    )


def safe_ui_call(component: str, operation: str, default: Any = None):
    """
    Decorator for safe UI operations that shouldn't crash the interface.

    Usage:
        @safe_ui_call("StatusBar", "render")
        def render(self):
            ...

    Args:
        component: Component name for logging
        operation: Operation name for logging
        default: Default return value on failure
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                log_exception(component, operation, e)
                return default
        return wrapper
    return decorator


class UIErrorContext:
    """
    Context manager for UI operations that captures and logs errors.

    Usage:
        with UIErrorContext("StatusBar", "display"):
            status_bar.display()
    """

    def __init__(self, component: str, operation: str, reraise: bool = False):
        self.component = component
        self.operation = operation
        self.reraise = reraise
        self.error: Optional[Exception] = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val is not None:
            self.error = exc_val
            log_exception(self.component, self.operation, exc_val)

            if self.reraise:
                return False  # Re-raise exception
            return True  # Suppress exception

        return False


def log_ui_event(component: str, event: str, details: str = None):
    """Log a UI event for debugging."""
    msg = f"[{component}] {event}"
    if details:
        msg += f": {details}"
    ui_logger.debug(msg)


def log_performance(component: str, operation: str, duration_ms: float):
    """Log performance metrics for UI operations."""
    ui_logger.debug(f"[{component}] {operation} took {duration_ms:.2f}ms")


# Collected errors for diagnostic reporting
_collected_errors = []


def collect_error(component: str, operation: str, error: Exception):
    """Collect an error for later diagnostic reporting."""
    _collected_errors.append({
        "timestamp": datetime.now().isoformat(),
        "component": component,
        "operation": operation,
        "error_type": type(error).__name__,
        "error_message": str(error),
    })

    # Keep only last 100 errors
    if len(_collected_errors) > 100:
        _collected_errors.pop(0)

    log_exception(component, operation, error)


def get_collected_errors() -> list:
    """Get list of collected errors for diagnostics."""
    return _collected_errors.copy()


def clear_collected_errors():
    """Clear the collected errors list."""
    _collected_errors.clear()


def get_error_summary() -> str:
    """Get a summary of collected errors."""
    if not _collected_errors:
        return "No UI errors collected."

    summary = [f"UI Errors ({len(_collected_errors)} total):"]

    # Group by component
    by_component = {}
    for err in _collected_errors:
        comp = err["component"]
        if comp not in by_component:
            by_component[comp] = []
        by_component[comp].append(err)

    for comp, errors in by_component.items():
        summary.append(f"\n  {comp}: {len(errors)} errors")
        # Show last 3 unique error types
        error_types = list(set(e["error_type"] for e in errors[-5:]))
        for et in error_types[:3]:
            summary.append(f"    - {et}")

    return "\n".join(summary)
