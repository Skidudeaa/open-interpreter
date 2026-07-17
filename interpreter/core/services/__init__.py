"""Backend-agnostic services extracted from the respond() loop.

These are pure-logic units (no generator/yield semantics) that both the built-in
``respond()`` driver and the out-of-process ``hermes`` backend can call, so
OI's value-add features are not welded to one execution path. See
``.planning/HERMES_COMPLEMENTARY_PLAN.md``.
"""

from .system_message_builder import SystemMessageBuilder

__all__ = ["SystemMessageBuilder"]
