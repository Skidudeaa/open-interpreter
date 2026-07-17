# Handoff — respond() decomposition keystone + HERMES ChunkPipeline plan

**Date:** 2026-07-17 · **Branch:** `refactor/respond-keystone` (off `feat/reranker`, 0a83fa2f)

## State: DONE and verified

`respond()` decomposed per `/root/.claude/plans/respond-decomposition-keystone-proceed-fuzzy-wirth.md`.
`respond()`: ~1340 → 713 lines. Full suite verified clean (see "Verification").

**Commits on this branch (mine):** `ba5f5344`(golden) → `84a14918`(de-brittle) → `2c6898d8`(SystemMessageBuilder)
→ `427231e1`(MemoryRecorder) → `ac1dcbc9`(CodeGate) → `dba36a89`/`8ea20d43`/`e85baf92`/`bf496845`/`e07fe576`(slim helpers)
→ `5494be2b`(docstring) → `f8bccc1d`(validation fix) → `c0fc7aec`(ChunkPipeline plan docs).

**Parallel session** landed `aa68fb83 feat(memory): pre-prompting` on top of mine — adds a memory
preamble to `SystemMessageBuilder.build()`. Verified it does NOT break the golden/decomposition (22 green).

### Extracted services (the keystone payload — reusable by hermes)
- `interpreter/core/services/system_message_builder.py` — `SystemMessageBuilder`
- `interpreter/core/memory/recorder.py` — `MemoryRecorder` (record_code_execution / record_file_changes / commit_edits)
- `interpreter/core/validation/code_gate.py` — `CodeGate`
- Plus ~15 in-file helpers in `respond.py` (post-exec hooks, execution core, message build, loop helpers).

### Safety net
- `tests/core/test_respond_golden.py` — pins the 7 chunk shapes + `_mcp_continue` filtering across 6 flows.
  **This is the regression gate for any future respond/pipeline work.** NOTE: it mocks `llm.run`, so it does
  NOT exercise `convert_to_openai_messages` — real-LLM regressions need `tests/test_interpreter.py`.

## Two latent bugs found

1. **FIXED** (`f8bccc1d`): validation gate was a dead no-op (swapped args + `.get()` on a dataclass). Now
   validates for real; opt-in via `enable_validation`; invalid code → `[Validation]` chunks (non-blocking).
2. **FIXED** (`24e3cb89`): `MemoryRecorder.record_code_execution` was doubly broken — `EditType.OTHER` (absent
   from the enum) AND `language=` (not an `Edit` field) — so it silently no-op'd. Fixed to `EditType.UNKNOWN`
   with language in `user_intent`; verified end-to-end (real SemanticEditGraph records the edit + MEMORY_RECORD).

## HERMES ChunkPipeline — Phases 0-4 DONE (branch `feat/hermes-chunkpipeline`)

Executed `.planning/HERMES_CHUNKPIPELINE_PLAN.md` through Phase 4. Commits: `9c8af866`(P0 seam) →
`5744cc54`(P1 system-msg→hermes) → `60dc077a`(P2 FileChangeDetector) → `7e3f9276`(P3 MemoryMiddleware) →
`7507d7c6`(P4 ValidationMiddleware). New: `interpreter/core/pipeline/` (ChunkPipeline + Memory/Validation
middleware), `interpreter/core/memory/file_change_detector.py`. Wired at `_respond_and_store` via lazy
`chunk_pipeline` property; oi golden byte-identical throughout; all middleware no-op for oi (gated on
`backend=="hermes"`). **P3 verified end-to-end: a hermes turn writing a file adds a row to the real
graph.db (41→42).** OPEN: P1 not reproduced against a live hermes (needs uvx/network — verify hermes honors
the in-prompt system preamble); P4 is warn-only (git-rollback deferred). **Phase 5 (Option-B consolidation)
optional, not started.** Parallel session is landing memory-layer commits (pre-prompting/outcome/task-state)
on the same lineage — coordinate branch points.

## (superseded) Original next: HERMES ChunkPipeline

Executable plan: `.planning/HERMES_CHUNKPIPELINE_PLAN.md` (scope: `HERMES_CHUNKPIPELINE_SCOPE.md`).
Wire the 3 services into hermes via middleware at the `_respond_and_store` seam (`core.py:916-921`).
- **Option A (hermes-gated)** first — oi stays byte-identical. **Phases 0-3 = north-star delivery (~2.5d):**
  pipeline seam → SystemMessage→hermes → FileChangeDetector → MemoryMiddleware (hermes edits feed graph.db).
- Key verified facts: single seam covers TUI+async_core; ACP has NO system-prompt slot (prepend to first
  prompt, `hermes_backend.py:370`); hermes exec is out-of-process (memory/validation are post-hoc); tracing
  is oi-only (don't port). Start branch `feat/hermes-chunkpipeline` off this one.

## Gotchas
- Commit with `SKIP=ruff,ruff-format git commit` — pre-commit ruff trips on ~13 pre-existing `core.py` errors.
- Editing `respond.py`: `✓`/`✗` are stored as `✓`/`✗` literals; the Edit tool auto-swaps single-block
  matches but fails on blocks mixing multiple unicode chars (`↑`, `—`) — cut those in smaller chunks.
- Project merges locally — do NOT open PRs (see memory `feedback_no_pull_requests`).

## Verification (full suite, this branch)
476 passed, 22 skipped, **7 failed — ALL confirmed non-regressions** (checked against baseline 0a83fa2f):
`test_hallucinations`, `test_nested_loops` (fail on baseline — LLM asserts); `test_write_to_file` (passes in
isolation — flaky LLM); `test_server`, `test_authenticated_*` (network/timeout); `TestWorkflowCache` (known
pre-existing); `TavilyProvider::test_not_available_without_key` (fails on baseline — env has TAVILY key).
