"""
The terminal interface is just a view. Just handles the very top layer.
If you were to build a frontend this would be a way to do it.
"""

try:
    import readline
except ImportError:
    pass

import os
import random
import re
import subprocess
import tempfile
import time

from ..core.utils.scan_code import scan_code
from ..core.utils.system_debug_info import system_info
from ..core.utils.truncate_output import truncate_output

# Phase 2-4: Agent visualization, context panel, mode manager
from .components.agent_strip import AgentStrip
from .components.code_block import CodeBlock
from .components.code_navigator import BlockType, CodeNavigator
from .components.diff_block import show_diff
from .components.error_block import display_error
from .components.interactive_menu import interactive_choice, interactive_confirm
from .components.message_block import MessageBlock
from .components.prompt_block import PromptBlock
from .components.spinner_block import ThinkingSpinner
from .components.status_bar import FeaturesBanner, StatusBar
from .components.toast import ToastLevel, ToastManager

# Phase 0 UI Architecture: Event system for future backends
from .components.ui_events import EventType, UIEvent, chunk_to_event, get_event_bus
from .components.ui_mode_manager import UIModeManager
from .components.ui_state import AgentStatus, UIState
from .magic_commands import handle_magic_command
from .utils.check_for_package import check_for_package
from .utils.cli_input import cli_input
from .utils.display_output import display_output
from .utils.find_image_path import find_image_path
from .utils.ui_logger import UIErrorContext, log_ui_event
from .utils.voice_output import speak, stop_speaking

# Add examples to the readline history
examples = [
    "How many files are on my desktop?",
    "What time is it in Seattle?",
    "Make me a simple Pomodoro app.",
    "Open Chrome and go to YouTube.",
    "Can you set my system to light mode?",
]
random.shuffle(examples)
try:
    for example in examples:
        readline.add_history(example)
except Exception:
    # If they don't have readline, that's fine
    pass


def _fuzzy_find_file(filename: str, search_dir: str = ".") -> tuple[str | None, int]:
    """
    Find a file using fuzzy matching.

    Args:
        filename: The filename to search for (basename only)
        search_dir: Directory to search in

    Returns:
        Tuple of (matched_path, score) or (None, 0) if no good match

    WHY: Helps recover from typos like 'generat_today.sh' → 'generate_today.sh'
    TRADEOFF: Only searches current dir to stay fast; 95%+ auto-substitutes, 85%+ suggests.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return None, 0

    try:
        files = os.listdir(search_dir)
    except OSError:
        return None, 0

    target = os.path.basename(filename)
    best_match = None
    best_score = 0

    for f in files:
        # Skip directories, only match files
        full_path = os.path.join(search_dir, f)
        if not os.path.isfile(full_path):
            continue

        score = fuzz.ratio(target.lower(), f.lower())
        if score > best_score:
            best_score = score
            best_match = full_path

    return best_match, best_score


def expand_at_references(message: str) -> str:
    """
    Expand @path references by prepending file contents.

    Finds @filepath patterns and prepends the file contents as context.
    The original @filepath stays in the message for reference.
    Uses fuzzy matching to recover from typos (95%+ auto-subs, 85%+ suggests).

    Args:
        message: User message potentially containing @path references

    Returns:
        Message with file contents prepended as context blocks

    WHY: File references give users a quick way to include file context without copy-paste.
    TRADEOFF: Silent failure on missing files was confusing; now we report unresolved refs.
    """
    # Pattern: @ followed by path chars, not preceded by non-whitespace (skip emails)
    pattern = r"(?<!\S)@([\w./_~-]+)"
    matches = re.findall(pattern, message)

    if not matches:
        return message

    context_parts = []
    unresolved_paths = []
    fuzzy_suggestions = []
    seen_paths = set()

    for path in matches:
        if path in seen_paths:
            continue
        seen_paths.add(path)

        # Try multiple path resolutions
        candidates = [
            os.path.expanduser(path),  # Handle ~/path
            os.path.abspath(path),  # Handle relative paths
        ]
        # If path starts with ./ or ../, also try from home
        if path.startswith(("./", "../")):
            candidates.append(os.path.join(os.path.expanduser("~"), path))

        resolved = None
        for candidate in candidates:
            if os.path.isfile(candidate):
                resolved = candidate
                break

        # Fuzzy matching fallback
        if not resolved:
            search_dir = os.path.dirname(path) if os.path.dirname(path) else "."
            fuzzy_match, score = _fuzzy_find_file(path, search_dir)

            if fuzzy_match and score >= 95:
                # High confidence - auto-substitute
                resolved = fuzzy_match
                fuzzy_suggestions.append(
                    f"@{path} → @{os.path.basename(fuzzy_match)} (auto-corrected, {score:.0f}%)"
                )
            elif fuzzy_match and score >= 85:
                # Medium confidence - suggest but don't substitute
                fuzzy_suggestions.append(
                    f"@{path}: did you mean @{os.path.basename(fuzzy_match)}? ({score:.0f}%)"
                )

        if resolved:
            try:
                with open(resolved, encoding="utf-8") as f:
                    content = f.read()
                # Truncate very large files
                if len(content) > 100000:
                    content = content[:100000] + "\n... (truncated)"
                context_parts.append(f"--- {path} ---\n{content}\n--- end {path} ---")
            except (OSError, UnicodeDecodeError) as e:
                # Report read errors
                unresolved_paths.append(f"@{path} (error: {e})")
        else:
            unresolved_paths.append(f"@{path} (not found)")

    result_parts = []

    # Report fuzzy matches/suggestions first
    if fuzzy_suggestions:
        result_parts.append("[Fuzzy matching: " + "; ".join(fuzzy_suggestions) + "]")

    # Report unresolved references
    if unresolved_paths:
        warning = "[File references not resolved: " + ", ".join(unresolved_paths) + "]"
        result_parts.append(warning)

    # Add resolved file contents
    if context_parts:
        result_parts.extend(context_parts)

    # Add original message
    result_parts.append(message)

    return "\n\n".join(result_parts)


def terminal_interface(interpreter, message):
    # Auto run and offline (this.. this isn't right) don't display messages.
    # Probably worth abstracting this to something like "debug_cli" at some point.
    # If (len(interpreter.messages) == 1), they probably used the advanced "i {command}" entry, so no message should be displayed.
    if (
        not interpreter.auto_run
        and not interpreter.offline
        and not (len(interpreter.messages) == 1)
    ):
        interpreter_intro_message = [
            "**Open Interpreter** will require approval before running code."
        ]

        if interpreter.safe_mode == "ask" or interpreter.safe_mode == "auto":
            if not check_for_package("semgrep"):
                interpreter_intro_message.append(
                    f"**Safe Mode**: {interpreter.safe_mode}\n\n>Note: **Safe Mode** requires `semgrep` (`pip install semgrep`)"
                )
        else:
            interpreter_intro_message.append("Use `interpreter -y` to bypass this.")

        if (
            not interpreter.plain_text_display
        ):  # A proxy/heuristic for standard in mode, which isn't tracked (but prob should be)
            interpreter_intro_message.append("Press `CTRL-C` to exit.")

        interpreter.display_message("\n\n".join(interpreter_intro_message) + "\n")

    # Display status bar at startup (if not in plain text mode)
    if not interpreter.plain_text_display:
        with UIErrorContext("StatusBar", "display"):
            status_bar = StatusBar(interpreter)
            status_bar.display()

        # Display features banner if any advanced features are enabled
        with UIErrorContext("FeaturesBanner", "display"):
            features_banner = FeaturesBanner(interpreter)
            features_banner.display()

    if message:
        interactive = False
    else:
        interactive = True

    active_block = None
    voice_subprocess = None

    # Phase 2-4: Initialize UI components
    ui_state = getattr(interpreter, "_ui_state", None) or UIState()
    mode_manager = UIModeManager(ui_state)
    toast_manager = ToastManager()
    agent_strip = AgentStrip(ui_state)
    code_navigator = CodeNavigator(ui_state)

    # Wire toast notifications to mode changes
    mode_manager.set_toast_handler(
        lambda msg: toast_manager.show(msg, level=ToastLevel.MODE)
    )

    # Subscribe to agent events to update UI state
    event_bus = get_event_bus()

    # Agent role icons for visual display
    _agent_icons = {
        "scout": "🔍",
        "surgeon": "🔧",
        "architect": "🏗️",
        "validator": "✅",
        "historian": "📚",
        "reviewer": "👁️",
        "tester": "🧪",
        "custom": "🤖",
    }

    # No inline printing - status is tracked in ui_state and displayed via Live panel

    def handle_agent_event(event: UIEvent):
        """Process agent events to update UI state (no printing - state only)."""
        if event.type == EventType.AGENT_SPAWN:
            from .components.ui_state import AgentRole

            agent_id = event.data.get("agent_id", "unknown")
            role_str = event.data.get("role", "custom")
            try:
                role = (
                    AgentRole(role_str)
                    if isinstance(role_str, str)
                    else AgentRole.CUSTOM
                )
            except ValueError:
                role = AgentRole.CUSTOM
            parent_id = event.data.get("parent_id")
            ui_state.add_agent(agent_id, role, parent_id)

        elif event.type == EventType.AGENT_COMPLETE:
            agent_id = event.data.get("agent_id")
            if agent_id:
                ui_state.update_agent_status(agent_id, AgentStatus.COMPLETE)
            ui_state.auto_purge_agents(max_age_seconds=300.0)

        elif event.type == EventType.AGENT_ERROR:
            agent_id = event.data.get("agent_id")
            error = event.data.get("error", "Unknown error")
            if agent_id:
                ui_state.update_agent_status(agent_id, AgentStatus.ERROR, error)

        elif event.type == EventType.AGENT_OUTPUT:
            agent_id = event.data.get("agent_id")
            line = event.data.get("line", "")
            if agent_id:
                ui_state.append_agent_output(agent_id, line)

        elif event.type == EventType.SYSTEM_TOKEN_UPDATE:
            ui_state.context_tokens = event.data.get("tokens", 0)
            ui_state.context_limit = event.data.get("limit", 128000)

        # Phase 3: Track code blocks for navigation
        if event.type == EventType.CODE_START:
            block_id = code_navigator.register_block(BlockType.CODE)
            # Store block_id for later reference
            ui_state._current_code_block_id = block_id

        elif event.type == EventType.CODE_END:
            # Register output block if there was output
            if hasattr(ui_state, "_current_code_block_id"):
                code_navigator.register_block(
                    BlockType.OUTPUT, parent_id=ui_state._current_code_block_id
                )

        elif event.type == EventType.MESSAGE_START:
            code_navigator.register_block(BlockType.MESSAGE)

        # === Feature feedback events ===
        # These provide visual cues when advanced features are active

        elif event.type == EventType.VALIDATION_START:
            toast_manager.show(
                "Validating syntax...", level=ToastLevel.INFO, duration=1.5
            )

        elif event.type == EventType.VALIDATION_END:
            is_valid = event.data.get("valid", True)
            error_count = event.data.get("error_count", 0)
            if is_valid:
                toast_manager.show(
                    "✓ Syntax valid", level=ToastLevel.SUCCESS, duration=2.0
                )
            else:
                toast_manager.show(
                    f"✗ {error_count} syntax error(s)",
                    level=ToastLevel.WARNING,
                    duration=3.0,
                )

        elif event.type == EventType.TRACING_START:
            toast_manager.show(
                "Tracing execution...", level=ToastLevel.INFO, duration=1.5
            )

        elif event.type == EventType.TRACING_END:
            success = event.data.get("success", True)
            call_count = event.data.get("call_count", 0)
            if success:
                toast_manager.show(
                    f"✓ Traced {call_count} calls",
                    level=ToastLevel.SUCCESS,
                    duration=2.0,
                )
            else:
                exc = event.data.get("exception", "error")
                toast_manager.show(
                    f"Trace exception: {exc}", level=ToastLevel.WARNING, duration=3.0
                )

        elif event.type == EventType.TEST_START:
            files_changed = event.data.get("files_changed", 0)
            toast_manager.show(
                f"Running tests for {files_changed} file(s)...",
                level=ToastLevel.INFO,
                duration=2.0,
            )

        elif event.type == EventType.TEST_END:
            passed = event.data.get("passed", 0)
            failed = event.data.get("failed", 0)
            if failed == 0 and passed > 0:
                toast_manager.show(
                    f"✓ {passed} test(s) passed", level=ToastLevel.SUCCESS, duration=3.0
                )
            elif failed > 0:
                toast_manager.show(
                    f"✗ {failed} test(s) failed", level=ToastLevel.ERROR, duration=4.0
                )

        elif event.type == EventType.MEMORY_RECORD:
            record_type = event.data.get("type", "edit")
            toast_manager.show(
                f"📝 Recorded to memory ({record_type})",
                level=ToastLevel.INFO,
                duration=1.5,
            )

        elif event.type == EventType.PLUGIN_HOOK:
            plugin_name = event.data.get("plugin", "unknown")
            hook_name = event.data.get("hook", "")
            toast_manager.show(
                f"🔌 {plugin_name}: {hook_name}", level=ToastLevel.INFO, duration=1.5
            )

        elif event.type == EventType.FILE_CHANGE:
            # Display diff for file changes made by code execution
            file_path = event.data.get("file_path", "")
            old_content = event.data.get("old_content", "")
            new_content = event.data.get("new_content", "")
            language = event.data.get("language", "text")

            # Show the diff
            if old_content != new_content and not interpreter.plain_text_display:
                try:
                    from rich.console import Console

                    console = Console()
                    console.print()  # Spacing
                    console.print(
                        f"  [bold cyan]📄 {file_path}[/bold cyan]", highlight=False
                    )
                    show_diff(old_content, new_content, language)
                except Exception as e:
                    log_ui_event("FILE_CHANGE", f"diff display failed: {e}")

        # Let mode manager process all events for auto-escalation
        mode_manager.process_event(event)

    # Subscribe to all agent-related events
    # Track subscribed event types for cleanup to prevent memory leaks
    _subscribed_events = [
        EventType.AGENT_SPAWN,
        EventType.AGENT_COMPLETE,
        EventType.AGENT_ERROR,
        EventType.AGENT_OUTPUT,
        EventType.SYSTEM_TOKEN_UPDATE,
        EventType.CODE_START,
        EventType.CODE_END,
        EventType.MESSAGE_START,
        EventType.SYSTEM_ERROR,
        EventType.CONSOLE_ERROR,
        # Feature feedback events (Phase 3+)
        EventType.VALIDATION_START,
        EventType.VALIDATION_END,
        EventType.TRACING_START,
        EventType.TRACING_END,
        EventType.TEST_START,
        EventType.TEST_END,
        EventType.MEMORY_RECORD,
        EventType.PLUGIN_HOOK,
        EventType.FILE_CHANGE,
    ]
    for event_type in _subscribed_events:
        event_bus.subscribe(event_type, handle_agent_event)

    def _cleanup_event_handlers():
        """Unsubscribe all event handlers to prevent memory leaks."""
        for event_type in _subscribed_events:
            event_bus.unsubscribe(event_type, handle_agent_event)
        # Also reset agent state to prevent accumulation
        ui_state.reset_agents()
        # Reset mode manager to prevent mode persistence across cleanups
        mode_manager.reset()

    # Track if this is a fresh conversation for mode reset
    _last_message_count = (
        len(interpreter.messages) if hasattr(interpreter, "messages") else 0
    )

    while True:
        # Reset mode manager at start of new conversations
        current_message_count = (
            len(interpreter.messages) if hasattr(interpreter, "messages") else 0
        )
        if current_message_count == 0 and _last_message_count > 0:
            # Conversation was cleared - reset mode to ZEN
            mode_manager.reset()
            ui_state.reset_agents()
        _last_message_count = current_message_count

        if interactive:
            if (
                len(interpreter.messages) == 1
                and interpreter.messages[-1]["role"] == "user"
                and interpreter.messages[-1]["type"] == "message"
            ):
                # They passed in a message already, probably via "i {command}"!
                message = interpreter.messages[-1]["content"]
                interpreter.messages = interpreter.messages[:-1]
            else:
                ### This is the primary input for Open Interpreter.
                try:
                    if interpreter.plain_text_display:
                        # Plain text mode: use simple input
                        message = (
                            cli_input("> ").strip()
                            if interpreter.multi_line
                            else input("> ").strip()
                        )
                    elif (
                        hasattr(interpreter, "_ui_backend")
                        and interpreter._ui_backend.supports_interactive
                    ):
                        # Use prompt_toolkit backend for interactive input (Phase 1)
                        ui_input = interpreter._ui_backend.get_input("❯ ")
                        message = (ui_input or "").strip()
                    else:
                        # Styled mode: use PromptBlock
                        prompt_style = (
                            "multiline" if interpreter.multi_line else "default"
                        )
                        prompt = PromptBlock(style=prompt_style)
                        message = prompt.input().strip()
                except (KeyboardInterrupt, EOFError):
                    # Treat Ctrl-D on an empty line the same as Ctrl-C by exiting gracefully
                    interpreter.display_message("\n\n`Exiting...`")
                    raise KeyboardInterrupt from None

            try:
                # This lets users hit the up arrow key for past messages
                readline.add_history(message)
            except Exception:
                # If the user doesn't have readline (may be the case on windows), that's fine
                pass

        if isinstance(message, str):
            # This is for the terminal interface being used as a CLI — messages are strings.
            # This won't fire if they're in the python package, display=True, and they passed in an array of messages (for example).

            if message == "":
                # Ignore empty messages when user presses enter without typing anything
                continue

            if message.startswith("%") and interactive:
                handle_magic_command(interpreter, message)
                continue

            # Many users do this
            if message.strip() == "interpreter --local":
                print("Please exit this conversation, then run `interpreter --local`.")
                continue
            if message.strip() == "pip install --upgrade open-interpreter":
                print(
                    "Please exit this conversation, then run `pip install --upgrade open-interpreter`."
                )
                continue

            if (
                interpreter.llm.supports_vision
                or interpreter.llm.vision_renderer is not None
            ):
                # Is the input a path to an image? Like they just dragged it into the terminal?
                image_path = find_image_path(message)

                ## If we found an image, add it to the message
                if image_path:
                    # Add the text interpreter's message history
                    interpreter.messages.append(
                        {
                            "role": "user",
                            "type": "message",
                            "content": message,
                        }
                    )

                    # Pass in the image to interpreter in a moment
                    message = {
                        "role": "user",
                        "type": "image",
                        "format": "path",
                        "content": image_path,
                    }

        # Rate limiting for UI refresh to prevent excessive rendering
        last_refresh_time = 0
        REFRESH_INTERVAL = 0.05  # 50ms = 20 refreshes/sec max

        # Initialize event bus for UI architecture (Phase 0)
        event_bus = get_event_bus()
        event_bus.emit(
            UIEvent(type=EventType.SYSTEM_START, source="terminal_interface")
        )

        try:
            # Start thinking spinner (only in styled mode)
            thinking_spinner = None
            if not interpreter.plain_text_display:
                with UIErrorContext("ThinkingSpinner", "start"):
                    try:
                        thinking_spinner = ThinkingSpinner()
                        thinking_spinner.start("Thinking")
                    except Exception:
                        thinking_spinner = None  # Continue without spinner

            # Expand @file references to include file contents
            message = expand_at_references(message)

            for chunk in interpreter.chat(message, display=False, stream=True):
                yield chunk

                # Emit event for UI architecture (Phase 0)
                # This allows future backends to consume events without modifying legacy code
                ui_event = chunk_to_event(chunk)
                if ui_event:
                    event_bus.emit(ui_event)

                # Phase 2: Display agent strip when agents are active
                if not interpreter.plain_text_display and ui_state.agent_strip_visible:
                    current_time = time.time()
                    if current_time - last_refresh_time > REFRESH_INTERVAL:
                        with UIErrorContext("AgentStrip", "render"):
                            agent_panel = agent_strip.render()
                            if agent_panel:
                                from rich.console import Console

                                console = Console()
                                console.print(agent_panel, end="")
                        last_refresh_time = current_time

                # Stop spinner when a block is about to be created (start) or content arrives
                # Must stop before creating any new Live contexts to avoid Rich conflicts
                if thinking_spinner and (
                    "start" in chunk or ("content" in chunk and chunk.get("content"))
                ):
                    with UIErrorContext("ThinkingSpinner", "stop"):
                        thinking_spinner.stop()
                    thinking_spinner = None

                # Is this for thine eyes?
                if "recipient" in chunk and chunk["recipient"] != "user":
                    continue

                if interpreter.verbose:
                    print("Chunk in `terminal_interface`:", chunk)

                # Comply with PyAutoGUI fail-safe for OS mode
                # so people can turn it off by moving their mouse to a corner
                if interpreter.os:
                    if (
                        chunk.get("format") == "output"
                        and "failsafeexception" in chunk["content"].lower()
                    ):
                        print("Fail-safe triggered (mouse in one of the four corners).")
                        break

                if chunk["type"] == "review" and chunk.get("content"):
                    # Specialized models can emit a code review.
                    print(chunk.get("content"), end="", flush=True)

                # Execution notice
                if chunk["type"] == "confirmation":
                    if not interpreter.auto_run:
                        # OI is about to execute code. The user wants to approve this

                        # CRITICAL: Stop thinking spinner before any user interaction
                        # to prevent Rich Live context conflicts
                        if thinking_spinner:
                            with UIErrorContext(
                                "ThinkingSpinner", "stop_for_confirmation"
                            ):
                                thinking_spinner.stop()
                            thinking_spinner = None

                        # End the active code block so you can run input() below it
                        if active_block and not interpreter.plain_text_display:
                            active_block.refresh(cursor=False)
                            active_block.end()
                            active_block = None

                        code_to_run = chunk["content"]
                        language = code_to_run["format"]
                        code = code_to_run["content"]

                        should_scan_code = False

                        if not interpreter.safe_mode == "off":
                            if interpreter.safe_mode == "auto":
                                should_scan_code = True
                            elif interpreter.safe_mode == "ask":
                                if interpreter.plain_text_display:
                                    response = input(
                                        "  Would you like to scan this code? (y/n)\n\n  "
                                    )
                                    if response.strip().lower() == "y":
                                        should_scan_code = True
                                else:
                                    # Use interactive confirmation menu
                                    should_scan_code = interactive_confirm(
                                        "Scan this code for security issues?",
                                        default=False,
                                    )

                        if should_scan_code:
                            scan_code(code, language, interpreter)

                        if interpreter.plain_text_display:
                            response = input(
                                "Would you like to run this code? (y/n)\n\n"
                            )
                            print("")  # <- Aesthetic choice
                        else:
                            # Use interactive menu for code execution confirmation
                            choice = interactive_choice(
                                options=["Run code", "Skip", "Edit code"],
                                title=f"Execute {language} code?",
                                descriptions=[
                                    "Execute the code block",
                                    "Skip execution and continue",
                                    "Edit code before running",
                                ],
                                default=0,
                            )
                            # Map choice to response
                            response = {0: "y", 1: "n", 2: "e"}.get(choice, "n")

                        if response.strip().lower() == "y":
                            # Create a new, identical block where the code will actually be run
                            # Conveniently, the chunk includes everything we need to do this:
                            active_block = CodeBlock(interpreter)
                            active_block.margin_top = False  # <- Aesthetic choice
                            active_block.language = language
                            active_block.code = code
                        elif response.strip().lower() == "e":
                            # Edit
                            original_code = code  # Save original for diff
                            tf_name = None

                            try:
                                # Create a temporary file
                                with tempfile.NamedTemporaryFile(
                                    suffix=".tmp", delete=False
                                ) as tf:
                                    tf.write(code.encode())
                                    tf.flush()
                                    tf_name = tf.name

                                # Open the temporary file with the default editor
                                subprocess.call(
                                    [os.environ.get("EDITOR", "vim"), tf_name]
                                )

                                # Read the modified code
                                with open(tf_name) as tf:
                                    code = tf.read()

                                # Show diff if code was changed
                                if (
                                    code != original_code
                                    and not interpreter.plain_text_display
                                ):
                                    log_ui_event("CodeEdit", "showing diff")
                                    show_diff(original_code, code, language)

                                interpreter.messages[-1][
                                    "content"
                                ] = code  # Give it code
                            finally:
                                # Delete the temporary file
                                if tf_name and os.path.exists(tf_name):
                                    os.unlink(tf_name)

                            active_block = CodeBlock()
                            active_block.margin_top = False  # <- Aesthetic choice
                            active_block.language = language
                            active_block.code = code
                        else:
                            # User declined to run code.
                            print(
                                "\n[Code execution declined. The assistant will be informed.]\n"
                            )
                            interpreter.messages.append(
                                {
                                    "role": "user",
                                    "type": "message",
                                    "content": "I have declined to run this code. Please continue with an alternative approach or explain what the code would have done.",
                                }
                            )
                            # Don't break - let the loop continue so the assistant can respond
                            continue

                # Plain text mode
                if interpreter.plain_text_display:
                    if "start" in chunk or "end" in chunk:
                        print("")
                    if chunk["type"] in ["code", "console"] and "format" in chunk:
                        if "start" in chunk:
                            print("```" + chunk["format"], flush=True)
                        if "end" in chunk:
                            print("```", flush=True)
                    if chunk.get("format") != "active_line":
                        print(chunk.get("content", ""), end="", flush=True)
                    continue

                if "end" in chunk and active_block:
                    active_block.refresh(cursor=False)

                    if chunk["type"] in [
                        "message",
                        "console",
                    ]:  # We don't stop on code's end — code + console output are actually one block.
                        # Set final execution status if this is a code block
                        if (
                            hasattr(active_block, "status")
                            and active_block.status == "running"
                        ):
                            # Check output for error indicators
                            output = getattr(active_block, "output", "")
                            if (
                                "Traceback" in output
                                or "Error" in output
                                or "Exception" in output
                            ):
                                active_block.status = "error"
                            else:
                                active_block.status = "success"
                        active_block.end()
                        active_block = None

                # Assistant message blocks
                if chunk["type"] == "message":
                    if "start" in chunk:
                        # Get role from chunk, default to assistant
                        role = chunk.get("role", "assistant")
                        active_block = MessageBlock(role=role)
                        render_cursor = True

                    if "content" in chunk:
                        active_block.message += chunk["content"]

                    if "end" in chunk and interpreter.os:
                        last_message = interpreter.messages[-1]["content"]

                        # Remove markdown lists and the line above markdown lists
                        lines = last_message.split("\n")
                        i = 0
                        while i < len(lines):
                            # Match markdown lists starting with hyphen, asterisk or number
                            if re.match(r"^\s*([-*]|\d+\.)\s", lines[i]):
                                del lines[i]
                                if i > 0:
                                    del lines[i - 1]
                                    i -= 1
                            else:
                                i += 1
                        message = "\n".join(lines)
                        # Replace newlines with spaces, escape double quotes and backslashes
                        sanitized_message = (
                            message.replace("\\", "\\\\")
                            .replace("\n", " ")
                            .replace('"', '\\"')
                        )

                        # Display notification in OS mode
                        interpreter.computer.os.notify(sanitized_message)

                        # Speak message aloud (cross-platform support)
                        if interpreter.speak_messages:
                            stop_speaking()  # Stop any ongoing speech
                            speak(sanitized_message, async_speak=True)

                # Assistant code blocks
                elif chunk["role"] == "assistant" and chunk["type"] == "code":
                    if "start" in chunk:
                        active_block = CodeBlock()
                        active_block.language = chunk["format"]
                        render_cursor = True

                    if "content" in chunk:
                        active_block.code += chunk["content"]

                # Computer can display visual types to user,
                # Which sometimes creates more computer output (e.g. HTML errors, eventually)
                if (
                    chunk["role"] == "computer"
                    and "content" in chunk
                    and (
                        chunk["type"] == "image"
                        or ("format" in chunk and chunk["format"] == "html")
                        or ("format" in chunk and chunk["format"] == "javascript")
                    )
                ):
                    if (interpreter.os) and (not interpreter.verbose):
                        # We don't display things to the user in OS control mode, since we use vision to communicate the screen to the LLM so much.
                        # But if verbose is true, we do display it!
                        continue

                    assistant_code_blocks = [
                        m
                        for m in interpreter.messages
                        if m.get("role") == "assistant" and m.get("type") == "code"
                    ]
                    if assistant_code_blocks:
                        code = assistant_code_blocks[-1].get("content")
                        if any(
                            text in code
                            for text in [
                                "computer.display.view",
                                "computer.display.screenshot",
                                "computer.view",
                                "computer.screenshot",
                            ]
                        ):
                            # If the last line of the code is a computer.view command, don't display it.
                            # The LLM is going to see it, the user doesn't need to.
                            continue

                    # Display and give extra output back to the LLM
                    extra_computer_output = display_output(chunk)

                    # We're going to just add it to the messages directly, not changing `recipient` here.
                    # Mind you, the way we're doing this, this would make it appear to the user if they look at their conversation history,
                    # because we're not adding "recipient: assistant" to this block. But this is a good simple solution IMO.
                    # we just might want to change it in the future, once we're sure that a bunch of adjacent type:console blocks will be rendered normally to text-only LLMs
                    # and that if we made a new block here with "recipient: assistant" it wouldn't add new console outputs to that block (thus hiding them from the user)

                    if (
                        interpreter.messages[-1].get("format") != "output"
                        or interpreter.messages[-1]["role"] != "computer"
                        or interpreter.messages[-1]["type"] != "console"
                    ):
                        # If the last message isn't a console output, make a new block
                        interpreter.messages.append(
                            {
                                "role": "computer",
                                "type": "console",
                                "format": "output",
                                "content": extra_computer_output,
                            }
                        )
                    else:
                        # If the last message is a console output, simply append the extra output to it
                        interpreter.messages[-1]["content"] += (
                            "\n" + extra_computer_output
                        )
                        interpreter.messages[-1]["content"] = interpreter.messages[-1][
                            "content"
                        ].strip()

                # Console
                if chunk["type"] == "console":
                    render_cursor = False
                    if "format" in chunk and chunk["format"] == "output":
                        # Use add_output for proper buffering (prevents scrolling chaos)
                        if hasattr(active_block, "add_output"):
                            active_block.add_output(chunk["content"])
                        else:
                            # Fallback for compatibility
                            active_block.output += "\n" + chunk["content"]
                            active_block.output = active_block.output.strip()

                        # Truncate output (only applies to final output string)
                        active_block.output = truncate_output(
                            active_block.output,
                            interpreter.max_output,
                            add_scrollbars=False,
                        )
                    if "format" in chunk and chunk["format"] == "active_line":
                        active_block.active_line = chunk["content"]

                        # Set status to running when execution starts
                        if (
                            hasattr(active_block, "status")
                            and active_block.status == "pending"
                        ):
                            active_block.status = "running"

                        # Display action notifications if we're in OS mode
                        if interpreter.os and active_block.active_line is not None:
                            action = ""

                            code_lines = active_block.code.split("\n")
                            if active_block.active_line < len(code_lines):
                                action = code_lines[active_block.active_line].strip()

                            if action.startswith("computer"):
                                description = None

                                # Extract arguments from the action
                                start_index = action.find("(")
                                end_index = action.rfind(")")
                                if start_index != -1 and end_index != -1:
                                    # (If we found both)
                                    arguments = action[start_index + 1 : end_index]
                                else:
                                    arguments = None

                                # NOTE: Do not put the text you're clicking on screen
                                # (unless we figure out how to do this AFTER taking the screenshot)
                                # otherwise it will try to click this notification!

                                if any(
                                    action.startswith(text)
                                    for text in [
                                        "computer.screenshot",
                                        "computer.display.screenshot",
                                        "computer.display.view",
                                        "computer.view",
                                    ]
                                ):
                                    description = "Viewing screen..."
                                elif action == "computer.mouse.click()":
                                    description = "Clicking..."
                                elif action.startswith("computer.mouse.click("):
                                    if "icon=" in arguments:
                                        text_or_icon = "icon"
                                    else:
                                        text_or_icon = "text"
                                    description = f"Clicking {text_or_icon}..."
                                elif action.startswith("computer.mouse.move("):
                                    if "icon=" in arguments:
                                        text_or_icon = "icon"
                                    else:
                                        text_or_icon = "text"
                                    if (
                                        "click" in active_block.code
                                    ):  # This could be better
                                        description = f"Clicking {text_or_icon}..."
                                    else:
                                        description = f"Mousing over {text_or_icon}..."
                                elif action.startswith("computer.keyboard.write("):
                                    description = f"Typing {arguments}."
                                elif action.startswith("computer.keyboard.hotkey("):
                                    description = f"Pressing {arguments}."
                                elif action.startswith("computer.keyboard.press("):
                                    description = f"Pressing {arguments}."
                                elif action == "computer.os.get_selected_text()":
                                    description = "Getting selected text."

                                if description:
                                    interpreter.computer.os.notify(description)

                    if "start" in chunk:
                        # We need to make a code block if we pushed out an HTML block first, which would have closed our code block.
                        if not isinstance(active_block, CodeBlock):
                            if active_block:
                                active_block.end()
                            active_block = CodeBlock()

                # Status indicators (features: validated, traced, recorded)
                # Skip start/end flag chunks that don't have content
                if (
                    chunk["type"] == "status"
                    and chunk.get("format") == "features"
                    and "content" in chunk
                ):
                    if active_block:
                        active_block.refresh(cursor=False)
                        active_block.end()
                        active_block = None
                    # Print status line in muted style
                    from rich.console import Console
                    from rich.text import Text

                    status_console = Console()
                    status_text = Text(f"  {chunk['content']}", style="dim")
                    status_console.print(status_text)

                if active_block:
                    # Rate-limited refresh to prevent UI unresponsiveness
                    current_time = time.time()
                    if current_time - last_refresh_time >= REFRESH_INTERVAL:
                        active_block.refresh(cursor=render_cursor)
                        last_refresh_time = current_time

            # (Sometimes -- like if they CTRL-C quickly -- active_block is still None here)
            if "active_block" in locals():
                if active_block:
                    active_block.end()
                    active_block = None
                    time.sleep(0.1)

            # Emit SYSTEM_END event (Phase 0)
            event_bus.emit(
                UIEvent(type=EventType.SYSTEM_END, source="terminal_interface")
            )

            if not interactive:
                # Don't loop - cleanup handlers before exiting
                _cleanup_event_handlers()
                break

        except KeyboardInterrupt:
            # Exit gracefully - stop spinner first
            if "thinking_spinner" in locals() and thinking_spinner:
                thinking_spinner.stop()
                thinking_spinner = None
            if "active_block" in locals() and active_block:
                active_block.end()
                active_block = None

            if interactive:
                # (this cancels LLM, returns to the interactive "> " input)
                continue
            else:
                # Cleanup handlers before exiting
                _cleanup_event_handlers()
                break
        except Exception:
            # Stop spinner on error to avoid terminal lock
            if "thinking_spinner" in locals() and thinking_spinner:
                thinking_spinner.stop()
                thinking_spinner = None
            if "active_block" in locals() and active_block:
                active_block.end()
                active_block = None

            import traceback

            error_text = traceback.format_exc()

            # Display structured error if not in plain text mode
            if not interpreter.plain_text_display:
                with UIErrorContext("ErrorBlock", "display"):
                    display_error(error_text)

            if interpreter.debug:
                system_info(interpreter)
            # Cleanup handlers before re-raising
            _cleanup_event_handlers()
            raise
