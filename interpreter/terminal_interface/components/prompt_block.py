"""
Prompt Block - Styled input handling for the terminal interface.

Features:
- Styled prompt symbols (❯ for default, ≫ for multiline)
- Themed confirmation dialogs
- Choice selection with styled options
"""

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.text import Text

from .theme import THEME, PROMPT_SYMBOLS


class PromptBlock:
    """
    Provides styled input prompts for the terminal interface.

    Prompt styles:
    - default: ❯ (cyan, for normal input)
    - multiline: ≫ (magenta, for multi-line mode)
    - confirmation: ? (yellow, for yes/no questions)
    """

    STYLE_COLORS = {
        "default": THEME["secondary"],      # Cyan
        "multiline": THEME["primary"],      # Violet
        "confirmation": THEME["warning"],   # Amber
    }

    def __init__(self, style: str = "default", console: Console = None):
        self.style = style
        self.console = console or Console()

    def get_styled_prompt(self) -> str:
        """Get the styled prompt string with markup."""
        symbol = PROMPT_SYMBOLS.get(self.style, PROMPT_SYMBOLS["default"])
        color = self.STYLE_COLORS.get(self.style, THEME["secondary"])
        return f"[bold {color}]{symbol}[/bold {color}] "

    def input(self, prompt_text: str = "") -> str:
        """
        Get styled input from the user.

        Args:
            prompt_text: Optional text to show before the prompt

        Returns:
            User input string
        """
        styled_prompt = self.get_styled_prompt()
        if prompt_text:
            full_prompt = f"{prompt_text}\n{styled_prompt}"
        else:
            full_prompt = styled_prompt

        return self.console.input(full_prompt)

    def confirm(self, message: str, default: bool = False) -> bool:
        """
        Styled confirmation dialog.

        Args:
            message: Question to ask
            default: Default value if user just presses enter

        Returns:
            True if user confirms, False otherwise
        """
        # Build styled message
        hint_yes = "[bold green]Y[/bold green]" if default else "[green]y[/green]"
        hint_no = "[red]n[/red]" if default else "[bold red]N[/bold red]"
        styled_message = f"[{THEME['warning']}]?[/{THEME['warning']}] {message} [{hint_yes}/{hint_no}]"

        return Confirm.ask(styled_message, default=default, console=self.console)

    def choice(self, message: str, choices: list, default: str = None) -> str:
        """
        Styled choice selection.

        Args:
            message: Question to ask
            choices: List of valid choices
            default: Default choice if user just presses enter

        Returns:
            Selected choice string
        """
        choice_str = "/".join(choices)
        styled_prompt = f"[{THEME['secondary']}]{PROMPT_SYMBOLS['default']}[/{THEME['secondary']}]"

        return Prompt.ask(
            f"{styled_prompt} {message} [{choice_str}]",
            choices=choices,
            default=default,
            console=self.console,
        )

    def code_confirmation(self, language: str = "code") -> str:
        """
        Styled code execution confirmation.

        Returns:
            'y' to run, 'n' to skip, 'e' to edit
        """
        message = "Would you like to run this code?"
        hint = "[bold green]y[/bold green]/[red]n[/red]/[cyan]e[/cyan]dit"
        styled_message = f"[{THEME['warning']}]?[/{THEME['warning']}] {message} [{hint}]"

        while True:
            response = Prompt.ask(
                styled_message,
                default="y",
                console=self.console,
            ).lower().strip()

            if response in ("y", "yes"):
                return "y"
            elif response in ("n", "no"):
                return "n"
            elif response in ("e", "edit"):
                return "e"
            else:
                self.console.print(
                    f"[{THEME['error']}]Please enter 'y', 'n', or 'e'[/{THEME['error']}]"
                )


def styled_input(prompt_text: str = "", style: str = "default") -> str:
    """
    Convenience function for quick styled input.

    Args:
        prompt_text: Optional text before prompt
        style: Prompt style (default/multiline/confirmation)

    Returns:
        User input string
    """
    return PromptBlock(style=style).input(prompt_text)


def styled_confirm(message: str, default: bool = False) -> bool:
    """
    Convenience function for quick styled confirmation.

    Args:
        message: Question to ask
        default: Default value

    Returns:
        True if confirmed, False otherwise
    """
    return PromptBlock(style="confirmation").confirm(message, default)
