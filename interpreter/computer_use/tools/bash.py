import asyncio
import os
import re
from typing import ClassVar, Literal

from anthropic.types.beta import BetaToolBash20241022Param

from .base import BaseAnthropicTool, CLIResult, ToolError, ToolResult

# Dangerous command patterns that require explicit approval
# Only truly destructive or irreversible operations
DANGEROUS_PATTERNS = [
    r'\brm\s+(-[rf]+\s+)*/($|\s)',      # rm -rf / (root filesystem)
    r'\brm\s+-[rf]*\s+--no-preserve-root', # explicit root deletion
    r'\bsudo\s+(rm|dd|mkfs|fdisk|parted)\b', # sudo with destructive commands
    r'\bchmod\s+(-R\s+)?777\s+/',       # chmod 777 on absolute paths (security risk)
    r'\bmkfs\b',                        # filesystem creation (destroys data)
    r'\bdd\s+.*of=\s*/dev/',            # dd writing to devices
    r'\b>\s*/dev/sd',                   # overwrite disk devices
    r'\bcurl\s+.*\|\s*(sudo\s+)?bash\b', # pipe to bash (arbitrary code execution)
    r'\bwget\s+.*\|\s*(sudo\s+)?bash\b', # pipe to bash
    r':\s*\(\)\s*\{',                   # fork bomb pattern
    r'\bgit\s+push\s+.*--force\s+origin\s+(main|master)\b', # force push to main
]

# Approval modes
APPROVAL_OFF = "off"          # No prompts (full auto-approve)
APPROVAL_DANGEROUS = "dangerous"  # Only prompt for dangerous commands
APPROVAL_ALL = "all"          # Prompt for everything


def is_dangerous_command(command: str) -> bool:
    """Check if a command matches dangerous patterns."""
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return True
    return False


def get_approval_mode() -> str:
    """Get the approval mode from environment variable."""
    mode = os.environ.get("OPEN_INTERPRETER_APPROVAL", "dangerous").lower()
    if mode in (APPROVAL_OFF, "0", "false", "no", "none"):
        return APPROVAL_OFF
    elif mode in (APPROVAL_ALL, "1", "true", "yes", "all"):
        return APPROVAL_ALL
    else:
        return APPROVAL_DANGEROUS  # default


class _BashSession:
    """A session of a bash shell."""

    _started: bool
    _process: asyncio.subprocess.Process

    command: str = "/bin/bash"
    _output_delay: float = 0.2  # seconds
    _timeout: float = 120.0  # seconds
    _sentinel: str = "<<exit>>"
    auto_approve: bool = False  # Skip user confirmation when True

    def __init__(self, auto_approve: bool = False):
        self._started = False
        self._timed_out = False
        self.auto_approve = auto_approve or os.environ.get("OPEN_INTERPRETER_AUTO_APPROVE", "").lower() in ("1", "true", "yes")

    async def start(self):
        if self._started:
            return

        self._process = await asyncio.create_subprocess_shell(
            self.command,
            preexec_fn=os.setsid,
            shell=True,
            bufsize=0,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        self._started = True

    def stop(self):
        """Terminate the bash shell."""
        if not self._started:
            raise ToolError("Session has not started.")
        if self._process.returncode is not None:
            return
        self._process.terminate()

    async def run(self, command: str):
        """Execute a command in the bash shell."""
        # Determine if we need approval based on mode and command risk
        approval_mode = get_approval_mode()
        needs_approval = False

        if approval_mode == APPROVAL_ALL:
            needs_approval = True
        elif approval_mode == APPROVAL_DANGEROUS:
            needs_approval = is_dangerous_command(command)
        # APPROVAL_OFF = no approval needed

        if needs_approval and not self.auto_approve:
            risk_label = "⚠️  DANGEROUS" if is_dangerous_command(command) else "Command"
            print(f"\n{risk_label}: {command}")
            try:
                user_input = input("Execute? [y/N]: ").strip().lower()
                if user_input not in ("y", "yes"):
                    return ToolResult(
                        system="Command execution cancelled by user",
                        error="User did not provide permission to execute the command.",
                    )
            except EOFError:
                # No TTY available, auto-approve
                pass

        if not self._started:
            raise ToolError("Session has not started.")
        if self._process.returncode is not None:
            return ToolResult(
                system="tool must be restarted",
                error=f"bash has exited with returncode {self._process.returncode}",
            )
        if self._timed_out:
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            )

        # we know these are not None because we created the process with PIPEs
        assert self._process.stdin
        assert self._process.stdout
        assert self._process.stderr

        # send command to the process
        self._process.stdin.write(
            command.encode() + f"; echo '{self._sentinel}'\n".encode()
        )
        await self._process.stdin.drain()

        # read output from the process, until the sentinel is found
        try:
            async with asyncio.timeout(self._timeout):
                while True:
                    await asyncio.sleep(self._output_delay)
                    # if we read directly from stdout/stderr, it will wait forever for
                    # EOF. use the StreamReader buffer directly instead.
                    output = (
                        self._process.stdout._buffer.decode()
                    )  # pyright: ignore[reportAttributeAccessIssue]
                    if self._sentinel in output:
                        # strip the sentinel and break
                        output = output[: output.index(self._sentinel)]
                        break
        except asyncio.TimeoutError:
            self._timed_out = True
            raise ToolError(
                f"timed out: bash has not returned in {self._timeout} seconds and must be restarted",
            ) from None

        if output.endswith("\n"):
            output = output[:-1]

        error = (
            self._process.stderr._buffer.decode()
        )  # pyright: ignore[reportAttributeAccessIssue]
        if error.endswith("\n"):
            error = error[:-1]

        # clear the buffers so that the next output can be read correctly
        self._process.stdout._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]
        self._process.stderr._buffer.clear()  # pyright: ignore[reportAttributeAccessIssue]

        return CLIResult(output=output, error=error)


class BashTool(BaseAnthropicTool):
    """
    A tool that allows the agent to run bash commands.
    The tool parameters are defined by Anthropic and are not editable.
    """

    _session: _BashSession | None
    name: ClassVar[Literal["bash"]] = "bash"
    api_type: ClassVar[Literal["bash_20241022"]] = "bash_20241022"
    auto_approve: bool = False  # Skip user confirmation when True

    def __init__(self, auto_approve: bool = False):
        self._session = None
        self.auto_approve = auto_approve
        super().__init__()

    async def __call__(
        self, command: str | None = None, restart: bool = False, **kwargs
    ):
        if restart:
            if self._session:
                self._session.stop()
            self._session = _BashSession(auto_approve=self.auto_approve)
            await self._session.start()

            return ToolResult(system="tool has been restarted.")

        if self._session is None:
            self._session = _BashSession(auto_approve=self.auto_approve)
            await self._session.start()

        if command is not None:
            return await self._session.run(command)

        raise ToolError("no command provided.")

    def to_params(self) -> BetaToolBash20241022Param:
        return {
            "type": self.api_type,
            "name": self.name,
        }
