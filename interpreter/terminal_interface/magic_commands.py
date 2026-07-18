import json
import os
import subprocess
import sys
from datetime import datetime

from ..core.utils.system_debug_info import system_info
from .local_setup import spinner_sleep
from .utils.count_tokens import count_messages_tokens
from .utils.export_to_markdown import export_to_markdown


def handle_undo(self, arguments):
    # Removes all messages after the most recent user entry (and the entry itself).
    # Therefore user can jump back to the latest point of conversation.
    # Also gives a visual representation of the messages removed.

    if len(self.messages) == 0:
        return
    # Find the index of the last 'role': 'user' entry
    last_user_index = None
    for i, message in enumerate(self.messages):
        if message.get("role") == "user":
            last_user_index = i

    removed_messages = []

    # Remove all messages after the last 'role': 'user'
    if last_user_index is not None:
        removed_messages = self.messages[last_user_index:]
        self.messages = self.messages[:last_user_index]

    print("")  # Aesthetics.

    # Print out a preview of what messages were removed.
    for message in removed_messages:
        if "content" in message and message["content"] is not None:
            if message.get("type") == "code":
                # Show code preview with language
                lang = message.get("format", "code")
                code_preview = message["content"][:60].replace("\n", " ")
                if len(message["content"]) > 60:
                    code_preview += "..."
                self.display_message(f"**Removed code** ({lang}): `{code_preview}`")
            else:
                self.display_message(
                    f"**Removed message:** `\"{message['content'][:30]}...\"`"
                )
        elif "function_call" in message:
            self.display_message("**Removed function call**")

    print("")  # Aesthetics.


def handle_help(self, arguments):
    commands_description = {
        "%% [commands]": "Run commands in system shell",
        "%verbose [true/false]": "Toggle verbose mode. Without arguments or with 'true', it enters verbose mode. With 'false', it exits verbose mode.",
        "%reset": "Resets the current session.",
        "%undo": "Remove previous messages and its response from the message history.",
        "%save_message [path]": "Saves messages to a specified JSON path. If no path is provided, it defaults to 'messages.json'.",
        "%load_message [path]": "Loads messages from a specified JSON path. If no path is provided, it defaults to 'messages.json'.",
        "%tokens [prompt]": "EXPERIMENTAL: Calculate the tokens used by the next request based on the current conversation's messages and estimate the cost of that request; optionally provide a prompt to also calculate the tokens used by that prompt and the total amount of tokens that will be sent with the next request",
        "%help": "Show this help message.",
        "%status": "Show current model, active features, context usage, and re-launch command.",
        "%retry": "Re-run the last user message (strips last exchange and re-queues it).",
        "%compact [n]": "Summarize old messages with the LLM to free context window. Keeps last n (default 6).",
        "%model [name]": "Show or hot-swap the model mid-session.",
        "%reflect [off]": "Escalate to a heavier reasoner (reflect_model) for deep thinking; %reflect again to revert.",
        "%copy": "Copy the last assistant response to the clipboard.",
        "%jump [pattern]": "Jump to a frecency-ranked directory (autojump-style). No args lists the top directories.",
        "%info": "Show system and interpreter information",
        "%jupyter": "Export the conversation to a Jupyter notebook file",
        "%markdown [path]": "Export the conversation to a specified Markdown path. If no path is provided, it will be saved to the Downloads folder with a generated conversation name.",
    }

    base_message = ["> **Available Commands:**\n\n"]

    # Add each command and its description to the message
    for cmd, desc in commands_description.items():
        base_message.append(f"- `{cmd}`: {desc}\n")

    additional_info = [
        "\n\nFor further assistance, please join our community Discord or consider contributing to the project's development."
    ]

    # Combine the base message with the additional info
    full_message = base_message + additional_info

    self.display_message("".join(full_message))


def handle_verbose(self, arguments=None):
    if arguments == "" or arguments == "true":
        self.display_message("> Entered verbose mode")
        print("\n\nCurrent messages:\n")
        for message in self.messages:
            message = message.copy()
            if message["type"] == "image" and message.get("format") not in [
                "path",
                "description",
            ]:
                message["content"] = (
                    message["content"][:30] + "..." + message["content"][-30:]
                )
            print(message, "\n")
        print("\n")
        self.verbose = True
    elif arguments == "false":
        self.display_message("> Exited verbose mode")
        self.verbose = False
    else:
        self.display_message("> Unknown argument to verbose command.")


def handle_debug(self, arguments=None):
    if arguments == "" or arguments == "true":
        self.display_message("> Entered debug mode")
        print("\n\nCurrent messages:\n")
        for message in self.messages:
            message = message.copy()
            if message["type"] == "image" and message.get("format") not in [
                "path",
                "description",
            ]:
                message["content"] = (
                    message["content"][:30] + "..." + message["content"][-30:]
                )
            print(message, "\n")
        print("\n")
        self.debug = True
    elif arguments == "false":
        self.display_message("> Exited verbose mode")
        self.debug = False
    else:
        self.display_message("> Unknown argument to debug command.")


def handle_auto_run(self, arguments=None):
    if arguments == "" or arguments == "true":
        self.display_message("> Entered auto_run mode")
        self.auto_run = True
    elif arguments == "false":
        self.display_message("> Exited auto_run mode")
        self.auto_run = False
    else:
        self.display_message("> Unknown argument to auto_run command.")


def handle_info(self, arguments):
    system_info(self)


def handle_reset(self, arguments):
    self.reset()
    self.display_message("> Reset Done")


def handle_status(self, arguments):
    """Show current session status: model, features, settings, and re-launch command."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    model = getattr(self.llm, "model", None) or "Not set"
    auto_run = getattr(self, "auto_run", False)
    safe_mode = getattr(self, "safe_mode", "off")

    features = {
        "memory": getattr(self, "enable_semantic_memory", False),
        "validation": getattr(self, "enable_validation", False),
        "agents": getattr(self, "enable_agents", False),
        "tracing": getattr(self, "enable_tracing", False),
        "plugins": getattr(self, "enable_plugins", False),
        "observability": getattr(self, "enable_observability", False),
    }

    ui_backend = getattr(self, "_ui_backend", None)
    backend_name = (
        type(ui_backend).__name__.replace("Backend", "") if ui_backend else "Default"
    )

    # Build a re-launch command the user can copy
    env_parts = []
    non_obs_features = {k: v for k, v in features.items() if k != "observability"}
    if any(non_obs_features.values()):
        if all(non_obs_features.values()):
            env_parts.append("OI_ACTIVATE_ALL=true")
        else:
            enabled = [k for k, v in non_obs_features.items() if v]
            env_parts.append(f"# features active: {', '.join(enabled)}")

    cmd_parts = ["poetry run interpreter"]
    if model and model != "Not set":
        cmd_parts.append(f"--model {model}")
    if auto_run:
        cmd_parts.append("-y")
    if features.get("observability"):
        cmd_parts.append("--observability")

    env_prefix = " ".join(env_parts) + " " if env_parts else ""
    relaunch = env_prefix + " ".join(cmd_parts)

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="dim", min_width=14)
    table.add_column()

    table.add_row("Model", f"[cyan]{model}[/cyan]")
    table.add_row("UI", f"[cyan]{backend_name}[/cyan]")
    table.add_row("Auto-run", "[green]on[/green]" if auto_run else "[dim]off[/dim]")
    if safe_mode != "off":
        table.add_row("Safe mode", f"[yellow]{safe_mode}[/yellow]")

    feat_parts = []
    for name, enabled in features.items():
        if enabled:
            feat_parts.append(f"[green]{name} ✓[/green]")
        else:
            feat_parts.append(f"[dim]{name} ✗[/dim]")
    table.add_row("Features", "  ".join(feat_parts))

    # Context window usage (if available)
    ui_state = getattr(self, "_ui_state", None)
    if ui_state and ui_state.context_tokens > 0:
        pct = ui_state.context_usage_percent
        used = ui_state.context_tokens
        limit = ui_state.context_limit
        color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
        context_line = (
            f"[{color}]{pct:.0f}%[/{color}]"
            f" [dim]({used:,} / {limit:,} tokens)[/dim]"
        )
        if pct >= 75:
            context_line += "  [dim]→ run %compact to summarize[/dim]"
        table.add_row("Context", context_line)

    table.add_row("", "")
    table.add_row("Re-launch", f"[yellow]{relaunch}[/yellow]")

    console.print(Panel(table, title="[bold]Session Status[/bold]", border_style="dim"))


def handle_retry(self, arguments):
    """Re-run the last user message (strips last exchange and re-queues it)."""
    last_user_idx = None
    for i, msg in enumerate(self.messages):
        if msg.get("role") == "user" and msg.get("type") == "message":
            last_user_idx = i

    if last_user_idx is None:
        self.display_message("> Nothing to retry")
        return

    last_msg = self.messages[last_user_idx].get("content", "")
    self.messages = self.messages[:last_user_idx]
    self._pending_retry = last_msg

    preview = last_msg[:70] + ("..." if len(last_msg) > 70 else "")
    self.display_message(f"> Retrying: `{preview}`")


def handle_compact(self, arguments):
    """Summarize old messages with the LLM to free up context window."""
    from rich.console import Console

    console = Console()

    try:
        keep_last = int(arguments.strip()) if arguments.strip() else 6
    except ValueError:
        self.display_message("> Usage: %compact [number of recent messages to keep]")
        return

    if len(self.messages) <= keep_last:
        self.display_message(
            f"> Not enough history to compact "
            f"({len(self.messages)} messages, keeping last {keep_last})"
        )
        return

    to_compact = self.messages[:-keep_last]
    to_keep = self.messages[-keep_last:]

    # Build text for the LLM — text messages only; code/console are noted but not full-quoted
    lines = []
    for msg in to_compact:
        role = msg.get("role", "?")
        mtype = msg.get("type", "message")
        content = msg.get("content") or ""
        label = "User" if role == "user" else "Assistant"
        if mtype == "message" and isinstance(content, str) and content.strip():
            lines.append(f"{label}: {content[:600]}")
        elif mtype == "code":
            lang = msg.get("format", "code")
            snippet = str(content)[:200]
            lines.append(f"{label} ran {lang} code: {snippet}")
        elif mtype == "console" and isinstance(content, str) and content.strip():
            lines.append(f"Console output: {content[:200]}")

    if not lines:
        self.display_message("> No text content found to compact")
        return

    console.print("[dim]Compacting conversation…[/dim]")

    try:
        import litellm

        litellm.suppress_debug_info = True
        response = litellm.completion(
            model=self.llm.model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Summarize this conversation history into a concise paragraph. "
                        "Preserve: file names, key code decisions, errors encountered, "
                        "what was resolved, and any open questions. "
                        "Start with: 'Earlier in this conversation:'\n\n"
                        + "\n\n".join(lines)
                    ),
                }
            ],
            max_tokens=700,
        )
        summary = response.choices[0].message.content
    except Exception as e:
        self.display_message(f"> Compact failed: {e}")
        return

    compacted = {"role": "user", "type": "message", "content": summary}
    self.messages = [compacted] + list(to_keep)

    saved = len(to_compact)
    console.print(
        f"[green]✓[/green] [dim]Compacted {saved} messages → 1 summary. "
        f"Kept last {len(to_keep)} messages.[/dim]"
    )


def handle_model(self, arguments):
    """Show or hot-swap the model mid-session."""
    from rich.console import Console

    console = Console()

    if not arguments.strip():
        current = getattr(self.llm, "model", "Not set")
        console.print(f"[dim]Model:[/dim] [cyan]{current}[/cyan]")
        console.print(
            "[dim]Switch:[/dim] [dim]%model gemini/gemini-3.1-pro-preview[/dim]"
        )
        return

    new_model = arguments.strip()
    old_model = getattr(self.llm, "model", "?")
    self.llm.model = new_model
    self.llm._is_loaded = False  # Force reload on next inference
    console.print(
        f"[dim]Model:[/dim] [red]{old_model}[/red] [dim]→[/dim] [green]{new_model}[/green]"
    )


def handle_reflect(self, arguments):
    """Toggle 'reflect' mode: hot-swap the main model to a heavier reasoner
    (self.reflect_model) on demand, then `%reflect` again (or `%reflect off`)
    to revert to the previous model."""
    from rich.console import Console

    console = Console()
    reflect_model = (
        getattr(self, "reflect_model", None) or "openrouter/moonshotai/kimi-k3"
    )
    prev = getattr(self, "_reflect_prev_model", None)
    arg = arguments.strip().lower()

    # Revert: explicit `off`, or toggle-off when already reflecting.
    if arg == "off" or (prev is not None and self.llm.model == reflect_model):
        if prev is not None:
            self.llm.model = prev
            self.llm._is_loaded = False
            self._reflect_prev_model = None
            console.print(f"[dim]Reflect off →[/dim] [green]{prev}[/green]")
        else:
            console.print("[dim]Not in reflect mode.[/dim]")
        return

    # Engage: stash the current model, swap to the reflect model.
    self._reflect_prev_model = self.llm.model
    self.llm.model = reflect_model
    self.llm._is_loaded = False
    console.print(
        f"[dim]Reflect on:[/dim] [red]{self._reflect_prev_model}[/red] [dim]→[/dim] "
        f"[magenta]{reflect_model}[/magenta] [dim](%reflect to revert)[/dim]"
    )


def handle_copy(self, arguments):
    """Copy the last assistant response to the clipboard."""
    from .utils.clipboard import copy_to_clipboard, get_last_content

    content = get_last_content(self)
    success, msg = copy_to_clipboard(content)
    if success:
        self.display_message(f"> Copied: `{msg}`")
    else:
        self.display_message(f"> Copy failed: {msg}")


def handle_jump(self, arguments):
    """Jump to a frecency-ranked directory, or list the top ones with no args."""
    frecency = self.computer.files.frecency
    pattern = (arguments or "").strip()

    if not pattern:
        top = frecency.top(10)
        if not top:
            self.display_message(
                "> No directories learned yet. They're recorded as you "
                "`cd` / os.chdir during sessions."
            )
            return
        lines = ["> **Top directories** (frecency):\n"]
        for path, weight in top:
            lines.append(f"- `{path}`  _(weight {weight:.1f})_\n")
        self.display_message("".join(lines))
        return

    try:
        target = self.computer.files.jump(pattern)
        self.display_message(f"> Jumped to `{target}`")
    except FileNotFoundError as e:
        self.display_message(f"> {e}")


def default_handle(self, arguments):
    self.display_message("> Unknown command")
    handle_help(self, arguments)


def handle_save_message(self, json_path):
    if json_path == "":
        json_path = "messages.json"
    if not json_path.endswith(".json"):
        json_path += ".json"
    with open(json_path, "w") as f:
        json.dump(self.messages, f, indent=2)

    self.display_message(f"> messages json export to {os.path.abspath(json_path)}")


def handle_load_message(self, json_path):
    if json_path == "":
        json_path = "messages.json"
    if not json_path.endswith(".json"):
        json_path += ".json"
    with open(json_path) as f:
        self.messages = json.load(f)

    self.display_message(f"> messages json loaded from {os.path.abspath(json_path)}")


def handle_count_tokens(self, prompt):
    messages = [{"role": "system", "message": self.system_message}] + self.messages

    outputs = []

    if len(self.messages) == 0:
        (conversation_tokens, conversation_cost) = count_messages_tokens(
            messages=messages, model=self.llm.model
        )
    else:
        (conversation_tokens, conversation_cost) = count_messages_tokens(
            messages=messages, model=self.llm.model
        )

    outputs.append(
        f"> Tokens sent with next request as context: {conversation_tokens} (Estimated Cost: ${conversation_cost})"
    )

    if prompt:
        (prompt_tokens, prompt_cost) = count_messages_tokens(
            messages=[prompt], model=self.llm.model
        )
        outputs.append(
            f"> Tokens used by this prompt: {prompt_tokens} (Estimated Cost: ${prompt_cost})"
        )

        total_tokens = conversation_tokens + prompt_tokens
        total_cost = conversation_cost + prompt_cost

        outputs.append(
            f"> Total tokens for next request with this prompt: {total_tokens} (Estimated Cost: ${total_cost})"
        )

    outputs.append(
        "**Note**: This functionality is currently experimental and may not be accurate. Please report any issues you find to the [Open Interpreter GitHub repository](https://github.com/OpenInterpreter/open-interpreter)."
    )

    self.display_message("\n".join(outputs))


def get_downloads_path():
    if os.name == "nt":
        # For Windows
        downloads = os.path.join(os.environ["USERPROFILE"], "Downloads")
    else:
        # For MacOS and Linux
        downloads = os.path.join(os.path.expanduser("~"), "Downloads")
        # For some GNU/Linux distros, there's no '~/Downloads' dir by default
        if not os.path.exists(downloads):
            os.makedirs(downloads)
    return downloads


def install_and_import(package):
    try:
        module = __import__(package)
    except ImportError:
        try:
            # Install the package silently with pip
            print("")
            print(f"Installing {package}...")
            print("")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", package],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            module = __import__(package)
        except subprocess.CalledProcessError:
            # If pip fails, try pip3
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "pip3", "install", package],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except subprocess.CalledProcessError:
                print(f"Failed to install package {package}.")
                return
    finally:
        globals()[package] = module
    return module


def jupyter(self, arguments):
    # Dynamically install nbformat if not already installed
    nbformat = install_and_import("nbformat")
    from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

    downloads = get_downloads_path()
    current_time = datetime.now()
    formatted_time = current_time.strftime("%m-%d-%y-%I%M%p")
    filename = f"open-interpreter-{formatted_time}.ipynb"
    notebook_path = os.path.join(downloads, filename)
    nb = new_notebook()
    cells = []

    for msg in self.messages:
        if msg["role"] == "user" and msg["type"] == "message":
            # Prefix user messages with '>' to render them as block quotes, so they stand out
            content = f"> {msg['content']}"
            cells.append(new_markdown_cell(content))
        elif msg["role"] == "assistant" and msg["type"] == "message":
            cells.append(new_markdown_cell(msg["content"]))
        elif msg["type"] == "code":
            # Handle the language of the code cell
            if "format" in msg and msg["format"]:
                language = msg["format"]
            else:
                language = "python"  # Default to Python if no format specified
            code_cell = new_code_cell(msg["content"])
            code_cell.metadata.update({"language": language})
            cells.append(code_cell)

    nb["cells"] = cells

    with open(notebook_path, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)

    print("")
    self.display_message(
        f"Jupyter notebook file exported to {os.path.abspath(notebook_path)}"
    )


def markdown(self, export_path: str):
    # If it's an empty conversations
    if len(self.messages) == 0:
        print("No messages to export.")
        return

    # If user doesn't specify the export path, then save the exported PDF in '~/Downloads'
    if not export_path:
        export_path = get_downloads_path() + f"/{self.conversation_filename[:-4]}md"

    export_to_markdown(self.messages, export_path)


def handle_magic_command(self, user_input):
    # Handle shell
    if user_input.startswith("%%"):
        code = user_input[2:].strip()
        self.computer.run("shell", code, stream=False, display=True)
        print("")
        return

    # split the command into the command and the arguments, by the first whitespace
    switch = {
        "help": handle_help,
        "verbose": handle_verbose,
        "debug": handle_debug,
        "auto_run": handle_auto_run,
        "reset": handle_reset,
        "save_message": handle_save_message,
        "load_message": handle_load_message,
        "undo": handle_undo,
        "tokens": handle_count_tokens,
        "info": handle_info,
        "status": handle_status,
        "retry": handle_retry,
        "compact": handle_compact,
        "model": handle_model,
        "reflect": handle_reflect,
        "copy": handle_copy,
        "jump": handle_jump,
        "jupyter": jupyter,
        "markdown": markdown,
    }

    user_input = user_input[1:].strip()  # Capture the part after the `%`
    command = user_input.split(" ")[0]
    arguments = user_input[len(command) :].strip()

    if command == "debug":
        print(
            "\n`%debug` / `--debug_mode` has been renamed to `%verbose` / `--verbose`.\n"
        )
        with spinner_sleep("Switching to verbose mode...", 1.5):
            pass
        command = "verbose"

    action = switch.get(
        command, default_handle
    )  # Get the function from the dictionary, or default_handle if not found
    action(self, arguments)  # Execute the function
