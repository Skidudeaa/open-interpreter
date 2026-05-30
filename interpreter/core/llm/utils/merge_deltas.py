def merge_deltas(original, delta):
    """
    Pushes the delta into the original and returns that.

    Great for reconstructing OpenAI streaming responses -> complete message objects.
    """

    for key, value in dict(delta).items():
        if value is not None:
            if isinstance(value, str):
                if key in original:
                    original[key] = (original[key] or "") + (value or "")
                else:
                    original[key] = value
            elif isinstance(value, list):
                # WHY: List-valued deltas (e.g. Gemini thinking_blocks,
                # annotations) aren't incrementally merged here — they arrive
                # complete, and OI captures tool_calls/signatures separately.
                # dict(value) would raise on a list, so replace wholesale.
                original[key] = value
            else:
                value = dict(value)
                if key not in original:
                    original[key] = value
                else:
                    merge_deltas(original[key], value)

    return original
