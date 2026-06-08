"""Pluggable execution backends for Open Interpreter.

The default backend is the built-in OI core loop (``interpreter.core.respond``).
Alternative backends drive an external agent while emitting the same LMC chunk
stream the rest of the system expects, so the terminal UI, EventBus, sidecar, and
approval flow keep working unchanged.

Currently available:

- ``hermes_backend`` — drives NousResearch hermes-agent out-of-process over the
  ACP (Agent Client Protocol) stdio JSON-RPC protocol.

Selected via ``interpreter.backend`` (``"oi"`` default, or ``"hermes"``), the
``OI_BACKEND`` environment variable, or the ``--backend`` CLI flag.
"""
