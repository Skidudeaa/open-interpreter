"""Runtime shim: surface Gemini thought-signatures during streaming.

WHY: Gemini 3.x hard-requires that the ``thoughtSignature`` attached to each
function-call part be replayed on every subsequent turn, or the API rejects the
request with 400 "Function call is missing a thought_signature in functionCall
parts." litellm 1.80's ``VertexGeminiConfig._create_streaming_choice`` builds
the streaming ``Delta`` WITHOUT ``thinking_blocks`` (the field that carries the
signature), so in streaming mode — which Open Interpreter always uses — the
signature is silently dropped and multi-turn tool calling breaks after the first
command. The non-streaming path keeps it, but switching off streaming would
break the whole streaming UI.

This wraps the method to copy ``thinking_blocks`` onto the streamed delta so
``run_tool_calling_llm`` can capture and replay the signature.

ARCHITECTURE: Applied once, lazily, right after litellm is first imported (from
``_get_litellm``). Idempotent and fully guarded — it only touches the Gemini
config, is a no-op for every other provider, and any failure leaves litellm
untouched rather than breaking LLM calls.
TRADEOFF: Reaches into litellm internals, so a future litellm refactor of
``_create_streaming_choice`` would silently disable the shim (the guard keeps it
safe, but the 400 would return). Tracked against litellm 1.80.0.
"""

import logging

logger = logging.getLogger(__name__)

# Marker attribute so we never double-wrap (e.g. if _get_litellm runs twice).
_PATCH_FLAG = "_oi_thinking_blocks_patched"


def apply_gemini_streaming_thinking_patch() -> None:
    """Patch litellm so Gemini streaming deltas expose ``thinking_blocks``.

    Safe to call repeatedly; later calls are no-ops once the patch is applied.
    """
    try:
        from litellm.llms.vertex_ai.gemini import (
            vertex_and_google_ai_studio_gemini as vertex_gemini,
        )
    except Exception:
        # litellm not installed, or its module layout changed. Nothing to patch.
        return

    config = getattr(vertex_gemini, "VertexGeminiConfig", None)
    if config is None:
        return

    original = getattr(config, "_create_streaming_choice", None)
    if original is None or getattr(original, _PATCH_FLAG, False):
        return  # Method missing (litellm changed) or already patched.

    def _patched_create_streaming_choice(*args, **kwargs):
        # NOTE: litellm calls this with keyword arguments, but accept both forms
        # so we survive a positional-call refactor without raising.
        choice = original(*args, **kwargs)
        try:
            ccm = kwargs.get("chat_completion_message")
            if ccm is None and args:
                ccm = args[0]
            thinking_blocks = (
                ccm.get("thinking_blocks") if isinstance(ccm, dict) else None
            )
            delta = getattr(choice, "delta", None)
            if thinking_blocks is not None and delta is not None:
                delta.thinking_blocks = thinking_blocks
        except Exception:
            # Capturing the signature must never break the stream itself.
            pass
        return choice

    setattr(_patched_create_streaming_choice, _PATCH_FLAG, True)
    config._create_streaming_choice = staticmethod(_patched_create_streaming_choice)
    logger.debug("Applied Gemini streaming thought-signature patch (litellm)")
