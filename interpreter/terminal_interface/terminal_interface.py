"""
The terminal interface is just a view. Just handles the very top layer.
If you were to build a frontend this would be a way to do it.
"""

try:
    import readline
except ImportError:
    pass

import os
import platform
import random
import re
import subprocess
import tempfile
import time

from ..core.utils.scan_code import scan_code
from ..core.utils.system_debug_info import system_info
from ..core.utils.truncate_output import truncate_output

# Phase 0 UI Architecture: Event system for future backends
from .components.ui_events import (
    UIEvent, EventType, get_event_bus, chunk_to_event
)
from .components.ui_state import UIState
from .components.sanitizer import sanitize_output

from .components.code_block import CodeBlock
from .components.diff_block import show_diff
from .components.error_block import display_error
from .components.interactive_menu import interactive_choice, interactive_confirm
from .components.message_block import MessageBlock
from .components.prompt_block import PromptBlock
from .components.spinner_block import ThinkingSpinner
from .components.status_bar import StatusBar, FeaturesBanner
from .magic_commands import handle_magic_command
from .utils.check_for_package import check_for_package
from .utils.cli_input import cli_input
from .utils.display_output import display_output
from .utils.find_image_path import find_image_path
from .utils.ui_logger import UIErrorContext, log_ui_event
from .utils.voice_output import speak, stop_speaking, check_tts_available

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
except:
    # If they don't have readline, that's fine
    pass


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

    while True:
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
                    elif hasattr(interpreter, '_ui_backend') and interpreter._ui_backend.supports_interactive:
                        # Use prompt_toolkit backend for interactive input (Phase 1)
                        message = interpreter._ui_backend.get_input("❯ ").strip()
                    else:
                        # Styled mode: use PromptBlock
                        prompt_style = "multiline" if interpreter.multi_line else "default"
                        prompt = PromptBlock(style=prompt_style)
                        message = prompt.input().strip()
                except (KeyboardInterrupt, EOFError):
                    # Treat Ctrl-D on an empty line the same as Ctrl-C by exiting gracefully
                    interpreter.display_message("\n\n`Exiting...`")
                    raise KeyboardInterrupt

            try:
                # This lets users hit the up arrow key for past messages
                readline.add_history(message)
            except:
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
                or interpreter.llm.vision_renderer != None
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
        event_bus.emit(UIEvent(type=EventType.SYSTEM_START, source="terminal_interface"))

        try:
            # Start thinking spinner (only in styled mode)
            thinking_spinner = None
            if not interpreter.plain_text_display:
                with UIErrorContext("ThinkingSpinner", "start"):
                    thinking_spinner = ThinkingSpinner()
                    thinking_spinner.start("Thinking")

            for chunk in interpreter.chat(message, display=False, stream=True):
                yield chunk

                # Emit event for UI architecture (Phase 0)
                # This allows future backends to consume events without modifying legacy code
                ui_event = chunk_to_event(chunk)
                if ui_event:
                    event_bus.emit(ui_event)

                # Stop spinner on first content chunk
                if thinking_spinner and ("content" in chunk or "start" in chunk):
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
                                        default=False
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
                                    "Edit code before running"
                                ],
                                default=0
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

                            # Create a temporary file
                            with tempfile.NamedTemporaryFile(
                                suffix=".tmp", delete=False
                            ) as tf:
                                tf.write(code.encode())
                                tf.flush()

                            # Open the temporary file with the default editor
                            subprocess.call([os.environ.get("EDITOR", "vim"), tf.name])

                            # Read the modified code
                            with open(tf.name, "r") as tf:
                                code = tf.read()

                            # Show diff if code was changed
                            if code != original_code and not interpreter.plain_text_display:
                                log_ui_event("CodeEdit", "showing diff")
                                show_diff(original_code, code, language)

                            interpreter.messages[-1]["content"] = code  # Give it code

                            # Delete the temporary file
                            os.unlink(tf.name)
                            active_block = CodeBlock()
                            active_block.margin_top = False  # <- Aesthetic choice
                            active_block.language = language
                            active_block.code = code
                        else:
                            # User declined to run code.
                            print("\n[Code execution declined. The assistant will be informed.]\n")
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
                    if (interpreter.os == True) and (interpreter.verbose == False):
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
                        if hasattr(active_block, 'add_output'):
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

                        # Display action notifications if we're in OS mode
                        if interpreter.os and active_block.active_line != None:
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
                                    description = f"Getting selected text."

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
                if chunk["type"] == "status" and chunk.get("format") == "features" and "content" in chunk:
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
            event_bus.emit(UIEvent(type=EventType.SYSTEM_END, source="terminal_interface"))

            if not interactive:
                # Don't loop
                break

        except KeyboardInterrupt:
            # Exit gracefully
            if "active_block" in locals() and active_block:
                active_block.end()
                active_block = None

            if interactive:
                # (this cancels LLM, returns to the interactive "> " input)
                continue
            else:
                break
        except Exception:
            import traceback
            error_text = traceback.format_exc()

            # Display structured error if not in plain text mode
            if not interpreter.plain_text_display:
                with UIErrorContext("ErrorBlock", "display"):
                    display_error(error_text)

            if interpreter.debug:
                system_info(interpreter)
            raise
