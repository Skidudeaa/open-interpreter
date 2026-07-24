"""Regression: phantom nameless tool_call deltas must not swallow text content.

litellm <=1.80 misparses claude-sonnet-5 streams and emits a spurious
tool_call delta (name=None, arguments="{}", index=-1) before the real text
content. Treating it as a genuine tool call flipped function_call_detected,
which routed every later content delta into the code-safety-review branch —
the user saw only "[Model returned empty response...]".
"""

from litellm.types.utils import ChatCompletionDeltaToolCall, Delta, Function

from interpreter.core.core import OpenInterpreter
from interpreter.core.llm.run_tool_calling_llm import run_tool_calling_llm


def _chunk(delta):
    return {"choices": [{"delta": delta}]}


def _tool_call_delta(name, arguments, index=0, call_id=None, content=None):
    return Delta(
        content=content,
        tool_calls=[
            ChatCompletionDeltaToolCall(
                id=call_id,
                type="function",
                index=index,
                function=Function(name=name, arguments=arguments),
            )
        ],
    )


def _run(chunks):
    llm = OpenInterpreter().llm
    llm.completions = lambda **params: iter(chunks)
    return list(
        run_tool_calling_llm(
            llm,
            {"messages": [{"role": "user", "type": "message", "content": "2 plus 2"}]},
        )
    )


def test_phantom_nameless_tool_call_does_not_swallow_content():
    chunks = [
        _chunk(_tool_call_delta(name=None, arguments="{}", index=-1, content="")),
        _chunk(Delta(content="2 + 2 = 4")),
        _chunk(Delta(content=None)),
    ]

    out = _run(chunks)

    text = "".join(c.get("content", "") for c in out if c.get("type") == "message")
    assert "2 + 2 = 4" in text
    assert "empty response" not in text


def test_real_tool_call_still_streams_code():
    chunks = [
        _chunk(_tool_call_delta(name="execute", arguments="", call_id="toolu_1")),
        _chunk(
            _tool_call_delta(
                name=None,
                arguments='{"language": "python", "code": "print(2 + 2)"}',
            )
        ),
    ]

    out = _run(chunks)

    code = "".join(c.get("content", "") for c in out if c.get("type") == "code")
    assert "print(2 + 2)" in code
