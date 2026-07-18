# Running this fork — the short version

## Just run it

```bash
oi
```

That's it. The `oi` alias (in the README) is:

```bash
alias oi='OI_ACTIVATE_ALL=true OPEN_INTERPRETER_APPROVAL=dangerous interpreter'
```

Or without the alias:

```bash
OPEN_INTERPRETER_APPROVAL=dangerous poetry run interpreter
```

**You don't need `OI_ACTIVATE_ALL` anymore** — all features are persisted in
`~/.config/open-interpreter/settings.json` (see below), so a bare `interpreter`
already turns them on. `OPEN_INTERPRETER_APPROVAL=dangerous` just means
"don't prompt me before running code."

## The one secret you must set

The reranker (memory relevance) needs a Cohere key. Put it in your shell profile
so it's always there:

```bash
echo 'export COHERE_API_KEY=your-cohere-key' >> ~/.bashrc && source ~/.bashrc
```

Without it, memory still works — recall just falls back to recency order instead
of relevance ranking (no crash, no error).

## What's on (persisted in settings.json — nothing to memorize)

`~/.config/open-interpreter/settings.json` already has:

| Setting | On? | What it does |
|---|---|---|
| `enable_semantic_memory` | ✅ | records your code edits to `~/.config/open-interpreter/semantic_graph.db` |
| `enable_memory_preprompt` | ✅ | injects relevant past memories into the system message each turn |
| `enable_preference_memory` | ✅ | learns "I prefer / never X" declarations |
| `enable_task_memory` | ✅ | tracks "let's X" / "done with X" |
| `enable_outcome_memory` | ✅ | remembers execution failures, warns on repeats |
| `enable_context_memory` | ✅ | infers time-of-day work patterns |
| `enable_reranker` | ✅ | Cohere relevance ranking for all recall |
| `enable_validation` | ✅ | syntax-checks edits |
| `enable_agents` | ✅ | Scout / Surgeon / Architect orchestration |
| `enable_observability` | ✅ | feeds the cc-sidecar daemon |

To turn any of them off, flip it to `false` in that file. To toggle in a session:
`interpreter.enable_task_memory = False`.

## Models in use

| Role | Model | Override |
|---|---|---|
| Main chat | `gemini/gemini-3.1-pro-preview` | `"model"` in settings.json; `--model X` / `OI_MODEL=X` per run |
| Scout (explore) | `gemini/gemini-3.5-flash` | `orchestrator.py::_ROLE_MODELS` |
| Surgeon (edits) | `gpt-5.6-terra` | ″ |
| Architect (design) | `claude-fable-5` | ″ |
| Validator | `claude-sonnet-5` | ″ |
| Reranker | `cohere/rerank-v4.0-pro` | `OI_RERANK_MODEL=X` |

The **main model is persisted** in `settings.json` (`"model"` key), so `oi` uses it
with no flags. Override per run with `interpreter --model ...` or `OI_MODEL=...`.

**Escalate on demand:** type `%reflect` mid-session to hot-swap to a heavier
reasoner (`reflect_model`, default `openrouter/moonshotai/kimi-k3`) for a hard
problem, then `%reflect` again (or `%reflect off`) to revert. Set the reflect
model with `OI_REFLECT_MODEL=...`.

## Backends

```bash
interpreter                    # built-in loop (default)
interpreter --backend hermes   # NousResearch hermes-agent (needs `uvx`; curl -LsSf https://astral.sh/uv/install.sh | sh)
```

Hermes gets the same memory + validation automatically (the ChunkPipeline +
shared system prompt handle it).

## Check memory is actually capturing

```bash
poetry run python3 -c "from interpreter import OpenInterpreter; i=OpenInterpreter(); print(i.semantic_graph.get_statistics())"
```

Shows total edits / files / symbols recorded.
