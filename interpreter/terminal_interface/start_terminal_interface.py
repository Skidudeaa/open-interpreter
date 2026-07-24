import argparse
import os
import sys
import time
from contextlib import contextmanager
from importlib.metadata import version

from rich.console import Console

from interpreter.terminal_interface.contributing_conversations import (
    contribute_conversation_launch_logic,
    contribute_conversations,
)

from .conversation_navigator import conversation_navigator
from .profiles.profiles import open_storage_dir, profile, reset_profile
from .utils.check_for_update import check_for_update
from .utils.session_manager import SessionManager, get_resume_prompt
from .validate_llm_settings import validate_llm_settings


# WHY: Users need visual feedback during sleeps. Spinners indicate activity.
# TRADEOFF: Minor complexity vs. better UX.
@contextmanager
def spinner_sleep(message: str, duration: float, console: Console | None = None):
    """Sleep with a visual spinner to indicate activity."""
    console = console or Console()
    with console.status(f"[cyan]{message}[/cyan]", spinner="dots"):
        time.sleep(duration)
        yield


def _ensure_sidecar_daemon(interpreter):
    """Ensure the cc-sidecar daemon is running so observability events land in
    SQLite instead of silently spooling to disk.

    WHY: --observability only starts the in-process EventBus bridge. The bridge
    sends events to a Unix socket served by a separate `cc-sidecar daemon`
    process. If that daemon isn't running, every event spools to
    ~/.cc-sidecar/spool and never materializes — making a healthy pipeline look
    broken. We start it on demand (best-effort) and print a one-line status.
    """
    import logging

    log = logging.getLogger(__name__)
    try:
        from cc_sidecar.ingest.transport import get_socket_path
    except Exception:
        # cc-sidecar package not importable — the bridge falls back to its
        # inline transport; nothing to auto-start.
        return

    socket_path = get_socket_path()
    if socket_path.exists():
        # A daemon (or stale socket) is already present. Trust a live socket;
        # the daemon replays the spool and dedupes, so we don't probe further.
        interpreter.display_message("> observability: daemon already up, capturing")
        return

    import shutil
    import subprocess

    cc_sidecar_bin = shutil.which("cc-sidecar")
    try:
        if cc_sidecar_bin:
            cmd = [cc_sidecar_bin, "daemon"]
        else:
            # Fall back to the module entry point in the current interpreter.
            cmd = [sys.executable, "-m", "cc_sidecar", "daemon"]
        # Detached, fire-and-forget: the daemon is long-lived and must outlive
        # this start-up path. We don't wait on it; the bridge spools until the
        # socket appears, then sends live.
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Give the daemon a moment to bind its socket so early events land live.
        for _ in range(20):
            if socket_path.exists():
                break
            time.sleep(0.1)
        if socket_path.exists():
            interpreter.display_message("> observability: daemon started, capturing")
        else:
            interpreter.display_message(
                "> observability: daemon starting — early events will spool then replay"
            )
    except Exception as e:
        log.warning("Could not auto-start cc-sidecar daemon: %s", e)
        interpreter.display_message(
            "> observability: daemon not running — events will spool. "
            "Start it with: cc-sidecar daemon"
        )


def start_terminal_interface(interpreter):
    """
    Meant to be used from the command line. Parses arguments, starts OI's terminal interface.
    """

    # Instead use an async interpreter, which has a server. Set settings on that
    if "--server" in sys.argv:
        from interpreter import AsyncInterpreter

        interpreter = AsyncInterpreter()

    arguments = [
        {
            "name": "profile",
            "nickname": "p",
            "help_text": "name of profile. run `--profiles` to open profile directory",
            "type": str,
            "default": "default.yaml",
        },
        {
            "name": "custom_instructions",
            "nickname": "ci",
            "help_text": "custom instructions for the language model. will be appended to the system_message",
            "type": str,
            "attribute": {"object": interpreter, "attr_name": "custom_instructions"},
        },
        {
            "name": "system_message",
            "nickname": "sm",
            "help_text": "(we don't recommend changing this) base prompt for the language model",
            "type": str,
            "attribute": {"object": interpreter, "attr_name": "system_message"},
        },
        {
            "name": "auto_run",
            "nickname": "y",
            "help_text": "automatically run generated code",
            "type": bool,
            "attribute": {"object": interpreter, "attr_name": "auto_run"},
        },
        {
            "name": "no_highlight_active_line",
            "nickname": "nhl",
            "help_text": "turn off active line highlighting in code blocks",
            "type": bool,
            "action": "store_true",
            "default": False,  # Default to False, meaning highlighting is on by default
        },
        {
            "name": "verbose",
            "nickname": "v",
            "help_text": "print detailed logs",
            "type": bool,
            "attribute": {"object": interpreter, "attr_name": "verbose"},
        },
        {
            "name": "debug_empty",
            "nickname": "de",
            "help_text": "debug blank/empty responses from the model",
            "type": bool,
            "attribute": {"object": interpreter, "attr_name": "debug_empty_responses"},
        },
        {
            "name": "model",
            "nickname": "m",
            "help_text": "language model to use",
            "type": str,
            "attribute": {"object": interpreter.llm, "attr_name": "model"},
        },
        {
            "name": "temperature",
            "nickname": "t",
            "help_text": "optional temperature setting for the language model",
            "type": float,
            "attribute": {"object": interpreter.llm, "attr_name": "temperature"},
        },
        {
            "name": "llm_supports_vision",
            "nickname": "lsv",
            "help_text": "inform OI that your model supports vision, and can receive vision inputs",
            "type": bool,
            "action": argparse.BooleanOptionalAction,
            "attribute": {"object": interpreter.llm, "attr_name": "supports_vision"},
        },
        {
            "name": "llm_supports_functions",
            "nickname": "lsf",
            "help_text": "inform OI that your model supports OpenAI-style functions, and can make function calls",
            "type": bool,
            "action": argparse.BooleanOptionalAction,
            "attribute": {"object": interpreter.llm, "attr_name": "supports_functions"},
        },
        {
            "name": "context_window",
            "nickname": "cw",
            "help_text": "optional context window size for the language model",
            "type": int,
            "attribute": {"object": interpreter.llm, "attr_name": "context_window"},
        },
        {
            "name": "max_tokens",
            "nickname": "x",
            "help_text": "optional maximum number of tokens for the language model",
            "type": int,
            "attribute": {"object": interpreter.llm, "attr_name": "max_tokens"},
        },
        {
            "name": "max_budget",
            "nickname": "b",
            "help_text": "optionally set the max budget (in USD) for your llm calls",
            "type": float,
            "attribute": {"object": interpreter.llm, "attr_name": "max_budget"},
        },
        {
            "name": "api_base",
            "nickname": "ab",
            "help_text": "optionally set the API base URL for your llm calls (this will override environment variables)",
            "type": str,
            "attribute": {"object": interpreter.llm, "attr_name": "api_base"},
        },
        {
            "name": "api_key",
            "nickname": "ak",
            "help_text": "optionally set the API key for your llm calls (this will override environment variables)",
            "type": str,
            "attribute": {"object": interpreter.llm, "attr_name": "api_key"},
        },
        {
            "name": "api_version",
            "nickname": "av",
            "help_text": "optionally set the API version for your llm calls (this will override environment variables)",
            "type": str,
            "attribute": {"object": interpreter.llm, "attr_name": "api_version"},
        },
        {
            "name": "max_output",
            "nickname": "xo",
            "help_text": "optional maximum number of characters for code outputs",
            "type": int,
            "attribute": {"object": interpreter, "attr_name": "max_output"},
        },
        {
            "name": "loop",
            "help_text": "runs OI in a loop, requiring it to admit to completing/failing task",
            "type": bool,
            "attribute": {"object": interpreter, "attr_name": "loop"},
        },
        {
            "name": "disable_telemetry",
            "nickname": "dt",
            "help_text": "disables sending of basic anonymous usage stats",
            "type": bool,
            "default": False,
            "attribute": {"object": interpreter, "attr_name": "disable_telemetry"},
        },
        {
            "name": "offline",
            "nickname": "o",
            "help_text": "turns off all online features (except the language model, if it's hosted)",
            "type": bool,
            "attribute": {"object": interpreter, "attr_name": "offline"},
        },
        {
            "name": "speak_messages",
            "nickname": "sp",
            "help_text": "(Mac only, experimental) use the applescript `say` command to read messages aloud",
            "type": bool,
            "attribute": {"object": interpreter, "attr_name": "speak_messages"},
        },
        {
            "name": "safe_mode",
            "nickname": "safe",
            "help_text": "optionally enable safety mechanisms like code scanning; valid options are off, ask, and auto",
            "type": str,
            "choices": ["off", "ask", "auto"],
            "default": "off",
            "attribute": {"object": interpreter, "attr_name": "safe_mode"},
        },
        {
            "name": "debug",
            "nickname": "debug",
            "help_text": "debug mode for open interpreter developers",
            "type": bool,
            "attribute": {"object": interpreter, "attr_name": "debug"},
        },
        {
            "name": "unsteer",
            "nickname": "u",
            "help_text": "enable Mistral-powered intent refinement to bypass overly sanitized LLM responses (requires OPENROUTER_API_KEY)",
            "type": bool,
            "action": argparse.BooleanOptionalAction,
            "attribute": {"object": interpreter, "attr_name": "enable_intent_refiner"},
        },
        {
            "name": "backend",
            "help_text": "execution backend: 'oi' (default, built-in loop) or 'hermes' (NousResearch hermes-agent via ACP; requires uvx)",
            "type": str,
            "choices": ["oi", "hermes"],
            "attribute": {"object": interpreter, "attr_name": "backend"},
        },
        {
            "name": "fast",
            "nickname": "f",
            "help_text": "runs `interpreter --model gpt-4o-mini` and asks OI to be extremely concise (shortcut for `interpreter --profile fast`)",
            "type": bool,
        },
        {
            "name": "multi_line",
            "nickname": "ml",
            "help_text": "enable multi-line inputs starting and ending with ```",
            "type": bool,
            "attribute": {"object": interpreter, "attr_name": "multi_line"},
        },
        {
            "name": "local",
            "nickname": "l",
            "help_text": "setup a local model (shortcut for `interpreter --profile local`)",
            "type": bool,
        },
        {
            "name": "codestral",
            "help_text": "shortcut for `interpreter --profile codestral`",
            "type": bool,
        },
        {
            "name": "assistant",
            "help_text": "shortcut for `interpreter --profile assistant.py`",
            "type": bool,
        },
        {
            "name": "llama3",
            "help_text": "shortcut for `interpreter --profile llama3`",
            "type": bool,
        },
        {
            "name": "groq",
            "help_text": "shortcut for `interpreter --profile groq`",
            "type": bool,
        },
        {
            "name": "creative",
            "help_text": "enable intent refinement with Mistral (shortcut for `interpreter --profile creative`)",
            "type": bool,
        },
        {
            "name": "vision",
            "nickname": "vi",
            "help_text": "experimentally use vision for supported languages (shortcut for `interpreter --profile vision`)",
            "type": bool,
        },
        {
            "name": "os",
            "nickname": "os",
            "help_text": "experimentally let Open Interpreter control your mouse and keyboard (shortcut for `interpreter --profile os`)",
            "type": bool,
        },
        # Special commands
        {
            "name": "reset_profile",
            "help_text": "reset a profile file. run `--reset_profile` without an argument to reset all default profiles",
            "type": str,
            "default": "NOT_PROVIDED",
            "nargs": "?",  # This means you can pass in nothing if you want
        },
        {"name": "profiles", "help_text": "opens profiles directory", "type": bool},
        {
            "name": "local_models",
            "help_text": "opens local models directory",
            "type": bool,
        },
        {
            "name": "conversations",
            "help_text": "list conversations to resume",
            "type": bool,
        },
        {
            "name": "server",
            "help_text": "start open interpreter as a server",
            "type": bool,
        },
        {
            "name": "version",
            "help_text": "get Open Interpreter's version number",
            "type": bool,
        },
        {
            "name": "contribute_conversation",
            "help_text": "let Open Interpreter use the current conversation to train an Open-Source LLM",
            "type": bool,
            "attribute": {
                "object": interpreter,
                "attr_name": "contribute_conversation",
            },
        },
        {
            "name": "plain",
            "nickname": "pl",
            "help_text": "set output to plain text",
            "type": bool,
            "attribute": {
                "object": interpreter,
                "attr_name": "plain_text_display",
            },
        },
        {
            "name": "stdin",
            "nickname": "s",
            "help_text": "Run OI in stdin mode",
            "type": bool,
        },
        {
            "name": "no_tui",
            "nickname": "nt",
            "help_text": "Disable interactive TUI (use basic Rich streaming)",
            "type": bool,
            "action": "store_true",
            "default": False,
        },
        {
            "name": "observability",
            "nickname": "ob",
            "help_text": "Enable cc-sidecar observability bridge (event logging to SQLite)",
            "type": bool,
            "action": "store_true",
            "default": False,
        },
        {
            "name": "tui",
            "nickname": "tu",
            "help_text": "Use experimental Textual TUI (full-screen app with mouse support)",
            "type": bool,
            "action": "store_true",
            "default": False,
        },
        {
            "name": "legacy_tui",
            "nickname": "lt",
            "help_text": "(Deprecated, now the default) Use prompt_toolkit TUI",
            "type": bool,
            "action": "store_true",
            "default": False,
        },
    ]

    if "--stdin" in sys.argv and "--plain" not in sys.argv:
        sys.argv += ["--plain"]

    # i shortcut
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        message = " ".join(sys.argv[1:])
        interpreter.messages.append(
            {"role": "user", "type": "message", "content": "I " + message}
        )
        sys.argv = sys.argv[:1]

        interpreter.custom_instructions = "UPDATED INSTRUCTIONS: You are in ULTRA FAST, ULTRA CERTAIN mode. Do not ask the user any questions or run code to gathet information. Go as quickly as you can. Run code quickly. Do not plan out loud, simply start doing the best thing. The user expects speed. Trust that the user knows best. Just interpret their ambiguous command as quickly and certainly as possible and try to fulfill it IN ONE COMMAND, assuming they have the right information. If they tell you do to something, just do it quickly in one command, DO NOT try to get more information (for example by running `cat` to get a file's infomration— this is probably unecessary!). DIRECTLY DO THINGS AS FAST AS POSSIBLE."

        files_in_directory = os.listdir()[:100]
        interpreter.custom_instructions += (
            "\nThe files in CWD, which THE USER MAY BE REFERRING TO, are: "
            + ", ".join(files_in_directory)
        )

        # interpreter.debug = True

    # Check for deprecated flags before parsing arguments
    deprecated_flags = {
        "--debug_mode": "--verbose",
    }

    for old_flag, new_flag in deprecated_flags.items():
        if old_flag in sys.argv:
            print(f"\n`{old_flag}` has been renamed to `{new_flag}`.\n")
            with spinner_sleep("Updating flag...", 1.5):
                pass
            sys.argv.remove(old_flag)
            sys.argv.append(new_flag)

    class CustomHelpParser(argparse.ArgumentParser):
        def print_help(self, *args, **kwargs):
            super().print_help(*args, **kwargs)
            special_help_message = '''
Open Interpreter, 2024

Use """ to write multi-line messages.
            '''
            print(special_help_message)

    parser = CustomHelpParser(
        description="Open Interpreter", usage="%(prog)s [options]"
    )

    # Add arguments
    for arg in arguments:
        default = arg.get("default")
        action = arg.get("action", "store_true")
        nickname = arg.get("nickname")

        name_or_flags = [f'--{arg["name"]}']
        if nickname:
            name_or_flags.append(f"-{nickname}")

        # Construct argument name flags
        flags = (
            [f"-{nickname}", f'--{arg["name"]}'] if nickname else [f'--{arg["name"]}']
        )

        if arg["type"] is bool:
            parser.add_argument(
                *flags,
                dest=arg["name"],
                help=arg["help_text"],
                action=action,
                default=default,
            )
        else:
            choices = arg.get("choices")
            parser.add_argument(
                *flags,
                dest=arg["name"],
                help=arg["help_text"],
                type=arg["type"],
                choices=choices,
                default=default,
                nargs=arg.get("nargs"),
            )

    args, unknown_args = parser.parse_known_args()

    # handle unknown arguments
    if unknown_args:
        print(f"\nUnrecognized argument(s): {unknown_args}")
        parser.print_usage()
        print(
            "For detailed documentation of supported arguments, please visit: https://docs.openinterpreter.com/settings/all-settings"
        )
        sys.exit(1)

    if args.profiles:
        open_storage_dir("profiles")
        return

    if args.local_models:
        open_storage_dir("models")
        return

    if args.reset_profile is not None and args.reset_profile != "NOT_PROVIDED":
        reset_profile(
            args.reset_profile
        )  # This will be None if they just ran `--reset_profile`
        return

    if args.version:
        oi_version = version("open-interpreter")
        update_name = "Developer Preview"  # Change this with each major update
        print(f"Open Interpreter {oi_version} {update_name}")
        return

    # Piped input (e.g. `echo "list files" | interpreter`): stdin is not a
    # TTY, so the interactive backends would start, read EOF, and exit without
    # ever seeing the piped message. Route through stdin mode as if --stdin
    # had been passed. plain_text_display also selects the RichStream backend.
    if not args.stdin and not args.server and not sys.stdin.isatty():
        args.stdin = True
        interpreter.plain_text_display = True

    if args.no_highlight_active_line:
        interpreter.highlight_active_line = False

    # if safe_mode and auto_run are enabled, safe_mode disables auto_run
    if interpreter.auto_run and (
        interpreter.safe_mode == "ask" or interpreter.safe_mode == "auto"
    ):
        interpreter.auto_run = False

    ### Set attributes on interpreter, so that a profile script can read the arguments passed in via the CLI

    set_attributes(args, arguments)

    ### Apply profile

    # Profile shortcuts, which should probably not exist:

    if args.fast:
        args.profile = "fast.yaml"

    if args.vision:
        args.profile = "vision.yaml"

    if args.os:
        args.profile = "os.py"

    if args.local:
        args.profile = "local.py"
        if args.vision:
            # This is local vision, set up moondream!
            interpreter.computer.vision.load()
        if args.os:
            args.profile = "local-os.py"

    if args.codestral:
        args.profile = "codestral.py"
        if args.vision:
            args.profile = "codestral-vision.py"
        if args.os:
            args.profile = "codestral-os.py"

    if args.assistant:
        args.profile = "assistant.py"

    if args.llama3:
        args.profile = "llama3.py"
        if args.vision:
            args.profile = "llama3-vision.py"
        if args.os:
            args.profile = "llama3-os.py"

    if args.groq:
        args.profile = "groq.py"

    if args.creative:
        args.profile = "creative.py"

    interpreter = profile(
        interpreter,
        args.profile or get_argument_dictionary(arguments, "profile")["default"],
    )

    ### Set attributes on interpreter, because the arguments passed in via the CLI should override profile

    set_attributes(args, arguments)
    interpreter.disable_telemetry = (
        os.getenv("DISABLE_TELEMETRY", "false").lower() == "true"
        or args.disable_telemetry
    )

    ### Set some helpful settings we know are likely to be true

    if interpreter.llm.model == "gpt-4" or interpreter.llm.model == "openai/gpt-4":
        if interpreter.llm.context_window is None:
            interpreter.llm.context_window = 6500
        if interpreter.llm.max_tokens is None:
            interpreter.llm.max_tokens = 4096
        if interpreter.llm.supports_functions is None:
            interpreter.llm.supports_functions = (
                False if "vision" in interpreter.llm.model else True
            )

    elif interpreter.llm.model.startswith("gpt-4") or interpreter.llm.model.startswith(
        "openai/gpt-4"
    ):
        if interpreter.llm.context_window is None:
            interpreter.llm.context_window = 123000
        if interpreter.llm.max_tokens is None:
            interpreter.llm.max_tokens = 4096
        if interpreter.llm.supports_functions is None:
            interpreter.llm.supports_functions = (
                False if "vision" in interpreter.llm.model else True
            )

    if interpreter.llm.model.startswith(
        "gpt-3.5-turbo"
    ) or interpreter.llm.model.startswith("openai/gpt-3.5-turbo"):
        if interpreter.llm.context_window is None:
            interpreter.llm.context_window = 16000
        if interpreter.llm.max_tokens is None:
            interpreter.llm.max_tokens = 4096
        if interpreter.llm.supports_functions is None:
            interpreter.llm.supports_functions = True

    ### Check for update

    try:
        if not interpreter.offline and not args.stdin:
            # This message should actually be pushed into the utility
            if check_for_update():
                interpreter.display_message(
                    "> **A new version of Open Interpreter is available.**\n>Please run: `pip install --upgrade open-interpreter`\n\n---"
                )
    except Exception:
        # Doesn't matter
        pass

    if interpreter.llm.api_base:
        if (
            not interpreter.llm.model.lower().startswith("openai/")
            and not interpreter.llm.model.lower().startswith("azure/")
            and not interpreter.llm.model.lower().startswith("ollama")
            and not interpreter.llm.model.lower().startswith("jan")
            and not interpreter.llm.model.lower().startswith("local")
        ):
            interpreter.llm.model = "openai/" + interpreter.llm.model
        elif interpreter.llm.model.lower().startswith("jan/"):
            # Strip jan/ from the model name
            interpreter.llm.model = interpreter.llm.model[4:]

    # If --conversations is used, run conversation_navigator
    if args.conversations:
        conversation_navigator(interpreter)
        return

    if interpreter.llm.model in [
        "claude-3.5",
        "claude-3-5",
        "claude-3.5-sonnet",
        "claude-3-5-sonnet",
    ]:
        interpreter.llm.model = "claude-3-5-sonnet-20240620"

    if not args.server:
        # This SHOULD RUN WHEN THE SERVER STARTS. But it can't rn because
        # if you don't have an API key, a prompt shows up, breaking the whole thing.
        validate_llm_settings(
            interpreter
        )  # This should actually just run interpreter.llm.load() once that's == to validate_llm_settings

    if args.server:
        interpreter.server.run()
        return

    interpreter.in_terminal_interface = True

    # --observability flag: enable cc-sidecar bridge without OI_ACTIVATE_ALL
    if getattr(args, "observability", False):
        interpreter.enable_observability = True

    # Initialize UI backend
    from .components.ui_backend import BackendType, create_backend
    from .components.ui_state import UIState

    ui_state = UIState()

    # Determine backend type based on CLI flags
    # Default: prompt_toolkit (reliable across Mac/iPad/SSH)
    # --tui: Experimental Textual full-screen TUI
    # --no-tui: Rich streaming (no interactive features, for pipes/CI)
    if args.no_tui or interpreter.plain_text_display:
        backend_type = BackendType.RICH_STREAM
    elif getattr(args, "tui", False):
        # WHY: Textual is opt-in because it requires full terminal control
        # and may not render on all terminals (iPad, some SSH clients).
        backend_type = BackendType.TEXTUAL
    else:
        # Default to prompt_toolkit — works reliably everywhere
        backend_type = BackendType.PROMPT_TOOLKIT

    backend = create_backend(interpreter, ui_state, force_type=backend_type)
    backend.start()

    # Store backend on interpreter for terminal_interface to use
    interpreter._ui_backend = backend
    interpreter._ui_state = ui_state

    # WHY: Touch the always-on session logger now so its EventBus sink is
    # attached before the first message — otherwise SYSTEM_START and the opening
    # exchange's terminal output are emitted before anyone is listening.
    try:
        _ = interpreter.session_logger
    except Exception:
        pass

    # WHY: Initialize the observability bridge early so it captures SYSTEM_START
    # and other events emitted before chat() is called. Without this, the bridge
    # only subscribes inside chat(), missing all pre-first-message events.
    if getattr(interpreter, "enable_observability", False):
        # WHY: The bridge fires events at a Unix socket, but nothing starts the
        # daemon that listens on it. Without a live daemon every event silently
        # spools to ~/.cc-sidecar/spool and `cc-sidecar status` shows nothing —
        # the pipeline looks broken when it is merely headless. Auto-start the
        # daemon (if absent) so --observability "just works", then report.
        _ensure_sidecar_daemon(interpreter)
        try:
            _ = interpreter.observability_bridge
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(
                "Observability bridge failed to initialize: %s", e
            )

    contribute_conversation_launch_logic(interpreter)

    try:
        # Standard in mode
        if args.stdin:
            # A pipe can carry multiple lines; read it all. Explicit --stdin
            # from a TTY keeps the original one-line input() behavior.
            stdin_input = input() if sys.stdin.isatty() else sys.stdin.read().strip()
            interpreter.plain_text_display = True
            if stdin_input:
                interpreter.chat(stdin_input)
        elif backend.backend_type == BackendType.TEXTUAL:
            # Textual has its own run loop that handles chat internally
            from .textual_backend import TextualBackend

            if isinstance(backend, TextualBackend):
                backend.run()  # Blocking - Textual handles everything
        else:
            interpreter.chat()
    finally:
        backend.stop()


def set_attributes(args, arguments):
    for argument_name, argument_value in vars(args).items():
        if argument_value is not None:
            if argument_dictionary := get_argument_dictionary(arguments, argument_name):
                if "attribute" in argument_dictionary:
                    attr_dict = argument_dictionary["attribute"]
                    setattr(attr_dict["object"], attr_dict["attr_name"], argument_value)

                    if args.verbose:
                        print(
                            f"Setting attribute {attr_dict['attr_name']} on {attr_dict['object'].__class__.__name__.lower()} to '{argument_value}'..."
                        )


def get_argument_dictionary(arguments: list[dict], key: str) -> dict:
    if (
        len(
            argument_dictionary_list := list(
                filter(lambda x: x["name"] == key, arguments)
            )
        )
        > 0
    ):
        return argument_dictionary_list[0]
    return {}


def main():
    from interpreter import interpreter

    # Session management
    session_mgr = SessionManager(interpreter)
    session_mgr.enable_autosave()

    # Check for resumable session. Skip when stdin is piped — the input()
    # below would consume the first line of the piped message as its answer.
    skip_resume = (
        any(arg in sys.argv for arg in ["-y", "--auto_run", "--version"])
        or not sys.stdin.isatty()
    )
    if not skip_resume and (resume_prompt := get_resume_prompt(interpreter)):
        try:
            response = input(f"\n{resume_prompt} ").strip().lower()
            if response == "y":
                sessions = session_mgr.list_sessions(limit=1)
                if sessions and session_mgr.load_session(sessions[0]["id"]):
                    print(
                        f"Resumed session with {len(interpreter.messages)} messages.\n"
                    )
        except (EOFError, KeyboardInterrupt):
            pass

    try:
        start_terminal_interface(interpreter)
    except KeyboardInterrupt:
        try:
            # Clear any Rich Live displays that may still be running
            # This prevents spinner artifacts from appearing during feedback prompts
            from rich.console import Console

            console = Console()
            try:
                console.clear_live()
            except IndexError:
                # Older rich raises on an empty live stack instead of no-op.
                pass

            # Save session on interrupt
            if interpreter.messages:
                session_mgr.save_session()

            interpreter.computer.terminate()

            if not interpreter.offline and not interpreter.disable_telemetry:
                feedback = None
                if len(interpreter.messages) > 3:
                    feedback = (
                        input("\n\nWas Open Interpreter helpful? (y/n): ")
                        .strip()
                        .lower()
                    )
                    if feedback == "y":
                        feedback = True
                    elif feedback == "n":
                        feedback = False
                    else:
                        feedback = None
                    if feedback is not None and not interpreter.contribute_conversation:
                        if interpreter.llm.model == "i":
                            contribute = "y"
                        else:
                            print(
                                "\nThanks for your feedback! Would you like to send us this chat so we can improve?\n"
                            )
                            contribute = input("(y/n): ").strip().lower()

                        if contribute == "y":
                            interpreter.contribute_conversation = True
                            interpreter.display_message(
                                "\n*Thank you for contributing!*\n"
                            )

                if (
                    interpreter.contribute_conversation or interpreter.llm.model == "i"
                ) and interpreter.messages != []:
                    conversation_id = (
                        interpreter.conversation_id
                        if hasattr(interpreter, "conversation_id")
                        else None
                    )
                    contribute_conversations(
                        [interpreter.messages], feedback, conversation_id
                    )

        except KeyboardInterrupt:
            pass
    finally:
        interpreter.computer.terminate()
