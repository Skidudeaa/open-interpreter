import sys

if "--os" in sys.argv:
    import threading

    from rich import print as rich_print
    from rich.markdown import Markdown
    from rich.rule import Rule

    def print_markdown(message):
        """
        Display markdown message. Works with multiline strings with lots of indentation.
        Will automatically make single line > tags beautiful.
        """

        for line in message.split("\n"):
            line = line.strip()
            if line == "":
                print("")
            elif line == "---":
                rich_print(Rule(style="white"))
            else:
                try:
                    rich_print(Markdown(line))
                except UnicodeEncodeError:
                    # Replace the problematic character or handle the error as needed
                    print("Error displaying line:", line)

        if "\n" not in message and message.startswith(">"):
            # Aesthetic choice. For these tags, they need a space below them
            print("")

    def _background_update_check():
        """
        Check for updates in background thread to avoid blocking startup.

        ARCHITECTURE: Non-blocking version check via daemon thread
        WHY: PyPI fetch can take 200-500ms; users shouldn't wait for it
        TRADEOFF: Update message may appear slightly after startup vs immediate
        """
        try:
            from importlib.metadata import version as get_version

            import requests
            from packaging import version

            response = requests.get(
                "https://pypi.org/pypi/open-interpreter/json", timeout=3
            )
            if response.ok:
                latest_version = response.json()["info"]["version"]
                current_version = get_version("open-interpreter")

                if version.parse(latest_version) > version.parse(current_version):
                    print_markdown(
                        "> **A new version of Open Interpreter is available.**\n>Please run: `pip install --upgrade open-interpreter`\n\n---"
                    )
        except Exception:
            # Non-blocking: silently ignore network/parse errors
            pass

    # Start background update check (daemon=True so it won't block exit)
    threading.Thread(target=_background_update_check, daemon=True).start()

    if "--voice" in sys.argv:
        print("Coming soon...")
    from .computer_use.loop import run_async_main

    run_async_main()
    exit()

from .core.agents import (
    AgentOrchestrator,
    ArchitectAgent,
    ResearchAgent,
    ResearchConfig,
    ScoutAgent,
    SurgeonAgent,
    ValidatorAgent,
)
from .core.async_core import AsyncInterpreter
from .core.computer.documents import Documents, ParsedDocument
from .core.computer.search import Search, SearchResult
from .core.computer.terminal.base_language import BaseLanguage
from .core.core import OpenInterpreter

# Export new modules for direct access
from .core.memory import ConversationLinker, Edit, EditType, SemanticEditGraph
from .core.tracing import CallGraph, ExecutionTrace, ExecutionTracer
from .core.validation import EditValidator, SyntaxChecker, ValidationResult

interpreter = OpenInterpreter()
computer = interpreter.computer

#     ____                      ____      __                            __
#    / __ \____  ___  ____     /  _/___  / /____  _________  ________  / /____  _____
#   / / / / __ \/ _ \/ __ \    / // __ \/ __/ _ \/ ___/ __ \/ ___/ _ \/ __/ _ \/ ___/
#  / /_/ / /_/ /  __/ / / /  _/ // / / / /_/  __/ /  / /_/ / /  /  __/ /_/  __/ /
#  \____/ .___/\___/_/ /_/  /___/_/ /_/\__/\___/_/  / .___/_/   \___/\__/\___/_/
#      /_/                                         /_/
