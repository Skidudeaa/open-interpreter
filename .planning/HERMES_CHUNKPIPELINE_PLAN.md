# HERMES ChunkPipeline — Executable Plan

**Branch:** `feat/hermes-chunkpipeline` (off `refactor/respond-keystone`) · **Scope doc:** `HERMES_CHUNKPIPELINE_SCOPE.md`

Wires the already-extracted services (SystemMessageBuilder, MemoryRecorder, CodeGate) into the
`hermes` backend via a middleware pipeline at the `_respond_and_store` seam, so OI observes and
learns from hermes runs. **Option A (hermes-gated)**: oi path stays byte-identical; the golden suite
(`tests/core/test_respond_golden.py`) is the regression gate for every phase. Verified facts and the
oi-double-execution rationale live in the scope doc — read it first.

## Ground rules (all phases)

- Work on `feat/hermes-chunkpipeline`. Commit per phase; full suite + golden green before moving on.
- Pre-commit trips ruff on pre-existing `core.py` errors → commit with `SKIP=ruff,ruff-format`.
- Middleware only *observes + appends*; never reorders the producer's chunks. Oi runs pass-through.
- Hermes-only logic gates on `getattr(interpreter, "backend", "oi") == "hermes"`.

---

## Phase 0 — Pipeline seam (no-op)

**New:** `interpreter/core/pipeline/__init__.py`, `interpreter/core/pipeline/base.py`.
```python
# base.py
class Middleware:
    def process(self, chunks, ctx):   # generator-transformer
        yield from chunks             # default pass-through

class ChunkPipeline:
    def __init__(self, middlewares=None): self._mw = list(middlewares or [])
    def process(self, chunks, ctx):
        for mw in self._mw:
            chunks = mw.process(chunks, ctx)
        return chunks
```
**`core.py`:** add lazy `chunk_pipeline` property mirroring `reranker` (`:532`) — `self._chunk_pipeline=None`
in `__init__`; property builds an **empty** `ChunkPipeline()` on first use. In `_respond_and_store`
(`:916–921`), after selecting `chunk_source`, wrap it:
```python
ctx = {"interpreter": self, "backend": getattr(self, "backend", "oi")}
chunk_source = self.chunk_pipeline.process(chunk_source, ctx)
```
**Tests:** `tests/core/pipeline/test_pipeline.py` — pass-through preserves order; empty pipeline is
identity. **Verify:** golden + `test_hermes_backend` unchanged. **Commit.**

---

## Phase 1 — SystemMessageBuilder → hermes

ACP has no system-prompt param (`acp_client.py:258/268`), so **prepend** OI's prompt to the first
user text. In `hermes_backend._drive` (`:370`), replace:
```python
await client.prompt(session_id, _latest_user_text(interpreter))
```
with a call that prefixes `SystemMessageBuilder().build(interpreter)` (as a labeled preamble) to the
user text. Keep it a small helper `_compose_prompt(interpreter)` in `hermes_backend.py`. oi untouched.
**Tests:** extend `test_hermes_backend` — assert the text sent to `client.prompt` contains the OI
system message (monkeypatch a fake ACPClient capturing `prompt`). **Verify:** hermes prompt carries
OI's prompt (incl. source-routing). **Commit.**

*Kill criterion:* if hermes ignores an in-prompt system preamble, escalate to extending `acp_client`
`new_session`/`initialize` with a protocol systemPrompt field — check the ACP spec before assuming.

---

## Phase 2 — FileChangeDetector service (behavior-identical for oi)

**New:** `interpreter/core/memory/file_change_detector.py` with a `FileChangeDetector`:
- `capture(cwd) -> dict` (wraps `capture_source_file_states`).
- `diff(before, after) -> dict` (wraps `diff_file_states`).
- `changes_since(before, cwd) -> dict` convenience.

Refactor `respond.py`: `_capture_file_snapshots_before` and the detection half of
`_detect_file_changes_and_commit` call the service (leave the FILE_CHANGE emit + MemoryRecorder calls
where they are). **This is oi-refactor — golden must stay identical.**
**Tests:** `tests/core/memory/test_file_change_detector.py` (round-trip a real edit). **Verify:**
golden unchanged. **Commit.**

---

## Phase 3 — MemoryMiddleware (hermes) — the north-star payoff

**New:** `interpreter/core/pipeline/memory_middleware.py`. `MemoryMiddleware.process(chunks, ctx)`:
- If `ctx["backend"] != "hermes"`: `yield from chunks` (oi already records inline). Pass-through.
- Else: `before = FileChangeDetector().capture(cwd)` at first chunk; `yield from chunks`; in a
  `finally`, `changed = FileChangeDetector().diff(before, capture(cwd))`, then
  `MemoryRecorder().record_file_changes(interpreter, changed, user_msg)` +
  `commit_edits(...)`, and emit `FILE_CHANGE` per change (reuse the emit block, or lift it into a
  shared helper). Snapshots bracket the **whole hermes turn** (coarser than oi per-cell — documented).

Register it in the `chunk_pipeline` property (Phase 0) so it's active for both backends but no-ops on oi.
**Tests:** `tests/core/pipeline/test_memory_middleware.py` — drive a fake hermes chunk stream that
"writes" a file in a tmp cwd; assert `record_file_changes` called + `MEMORY_RECORD`/`FILE_CHANGE`
events on the bus. **Verify (integration):** a real/faked hermes turn writing a file → row in
graph.db. **Verify:** golden unchanged (oi pass-through). **Commit.**

---

## Phase 4 — ValidationMiddleware (hermes, post-hoc, warn-only)

**New:** `interpreter/core/pipeline/validation_middleware.py`. Hermes exec is out-of-process, so
validate **after** the edit: for each changed file (from the same turn-end diff), run
`SyntaxChecker().check(content, file_path)` and, on failure, **inject** a `[Validation]` console
chunk (warn-only). No git rollback in this phase.
Gate on `backend == "hermes"`; oi pass-through.
**Tests:** `tests/core/pipeline/test_validation_middleware.py` — hermes turn producing an invalid
`.py` → injected `[Validation]` chunk + `VALIDATION_*` events. **Verify:** golden unchanged. **Commit.**

*Kill criterion / follow-up:* git-rollback-on-invalid is deferred; only add if warn-only proves
insufficient and rollback doesn't disrupt hermes UX.

---

## Phase 5 — (optional) Consolidate to backend-agnostic (Option B)

Only if 0–4 are stable and the single-source end state is wanted. Move oi's inline service calls
(memory recording, validation gate) out of `respond()` into the shared middleware so both backends
use one path; delete the duplicated inline calls. **Highest oi-regression risk** — the golden
sequence must stay byte-identical; do it as its own reviewed commit. Defaults to *not done*.

---

## Sequencing & verification

Order: 0 → 1 → 2 → 3 → (4) → (5). **Minimum north-star delivery = 0–3.**

- After every phase: `poetry run pytest tests/core/test_respond_golden.py tests/core/pipeline/ tests/core/test_hermes_backend.py tests/core/memory/ -q` green; oi golden byte-identical.
- Full suite before "done": `poetry run pytest -q` — the only expected failures are the known
  environmental/flaky ones (see `refactor/respond-keystone` verification: `TestWorkflowCache`,
  live-LLM `test_interpreter.py`, Tavily-key `test_search`). Confirm no NEW failures.
- North-star integration proof (Phase 3): drive a hermes turn that writes a file (fake ACPClient or
  live hermes if installed) and show the resulting `graph.db` row + sidecar events — not just unit
  mocks (per the "prove it with concrete evidence" rule).

## Not in scope

Agent-over-hermes (HERMES plan Phase 6 — separate re-entrancy design). Runtime tracing for hermes
(oi-only; `sys.settrace` can't cross the process boundary — document N/A, don't fake it).
