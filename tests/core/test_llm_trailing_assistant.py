"""Regression test: conversations sent to the provider must end with a user turn.

respond.py injects agent findings as a trailing *assistant* message before
re-running the LLM. Newer Anthropic models reject such conversations outright
("This model does not support assistant message prefill. The conversation must
end with a user message."). Llm.run() closes the turn with a neutral user nudge
whenever the outgoing message list would otherwise end on an assistant message.
"""

from interpreter.core.core import OpenInterpreter


def _prepared_llm(captured: dict):
    interp = OpenInterpreter()
    llm = interp.llm
    llm._is_loaded = True  # skip load() (network/model probing)
    llm.supports_functions = True
    llm.supports_vision = False
    llm.context_window = 8000
    llm.max_tokens = 1000

    def fake_completions(**request_params):
        captured.update(request_params)
        return iter(())

    llm.completions = fake_completions
    return llm


def test_trailing_assistant_message_gets_user_nudge():
    captured = {}
    llm = _prepared_llm(captured)

    messages = [
        {"role": "system", "type": "message", "content": "You are helpful."},
        {"role": "user", "type": "message", "content": "find the auth code"},
        {
            "role": "assistant",
            "type": "message",
            "content": "[Agent Results]\n**Files found (1):**\n- auth.py\n",
        },
    ]

    list(llm.run(messages))

    sent = captured["messages"]
    assert sent, "no messages were sent to the provider"
    assert sent[-1]["role"] == "user"


def test_user_final_message_is_untouched():
    captured = {}
    llm = _prepared_llm(captured)

    messages = [
        {"role": "system", "type": "message", "content": "You are helpful."},
        {"role": "user", "type": "message", "content": "hello"},
    ]

    list(llm.run(messages))

    sent = captured["messages"]
    assert sent[-1]["role"] == "user"
    # Exactly one user message — no synthetic nudge appended.
    assert sum(1 for m in sent if m["role"] == "user") == 1
