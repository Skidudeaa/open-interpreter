# HERMES ChunkPipeline — Scope

**Status:** Proposed (awaiting approval) · **Created:** 2026-07-17 · **Depends on:** `refactor/respond-keystone` (services extracted)

This scopes the `ChunkPipeline` half of `.planning/HERMES_COMPLEMENTARY_PLAN.md`. That plan's
service-extraction phases (SystemMessageBuilder, MemoryRecorder, CodeGate) are **done** on
`refactor/respond-keystone`. What remains is the *wiring*: a middleware pipeline at the backend
seam so the `hermes` backend reuses those services and OI **observes and learns from** hermes runs
(the north star). This doc re-scopes the pipeline against what the extraction actually left behind.

## What's already true (verified in code)

- **Single seam.** `core.py:_respond_and_store` (`:916–921`) selects `respond(self)` or
  `hermes_backend.run(self)` and drains it. Both the TUI (`_streaming_chat`) and `async_core`
  (`:146`, `:947`) consume this method, so wiring here covers every consumer.
- **Both backends yield the same 7 LMC chunk shapes.** `hermes_backend._translate_update`
  (`:70`) maps ACP `session/update` → `type:message` / `type:code` / `type:console`, and
  `_confirmation_chunk_from_toolcall` (`:126`) rebuilds the confirmation chunk. Pinned by
  `tests/core/test_respond_golden.py` (oi) and `test_hermes_backend.py:220` (hermes).
- **Two event sources today — this is the crux:**
  1. **Chunk-derived events** (`CODE_START/END`, `CONSOLE_OUTPUT`, `MESSAGE_*`): produced by
     `chunk_to_event()` (`ui_events.py:404`) at `terminal_interface.py:783` as chunks stream.
     Hermes **already** gets these on the TUI path (both backends flow through here).
  2. **Semantic feature-events** (`VALIDATION_*`, `MEMORY_RECORD`, `FILE_CHANGE`, `GIT_COMMIT`,
     `TEST_*`, `TRACING_*`): emitted **inline by the services/helpers** (`code_gate.py:44`,
     `recorder.py:68/139`, `respond.py:387/432`, `_execute_code`). Hermes gets **none** of these
     — not because of missing emits, but because **it never runs those features**.
- **`ObservabilityBridge` is backend-agnostic already.** It `subscribe_all`s the EventBus
  (`observability.py:144`); any emitter of `EventType.*` feeds the sidecar. No sidecar change needed.
- **Hermes sends NO system message.** `_drive` calls `client.prompt(session_id,
  _latest_user_text(interpreter))` (`hermes_backend.py:370`) — just the last user turn. OI's prompt
  (incl. source-routing) never reaches hermes.
- **Hermes executes out-of-process.** Code runs in the hermes subprocess, so OI cannot pre-gate
  execution or `sys.settrace` it. Memory/validation for hermes are necessarily **post-hoc**.

## Reframing: the pipeline runs *features*, not just events

Because the semantic events are a *byproduct* of running the features (the extracted services
already emit their own events), "observability for hermes" is not separable from "run memory +
validation for hermes." So the pipeline's real job is: **watch the backend's chunk stream (and the
turn boundaries) and drive the extracted services** — which then emit their events for free. This is
simpler than the original plan implied: no event-relocation, just service invocation.

## Mechanism

A `ChunkPipeline` of generator-transformer middleware, applied at the seam:

```python
# core.py _respond_and_store
chunk_source = hermes_backend.run(self) if backend == "hermes" else respond(self)
chunk_source = self.chunk_pipeline.process(chunk_source, ctx)   # NEW — both backends
for chunk in chunk_source:
    ...  # unchanged consumer below
```

- `Middleware.process(chunks, ctx) -> Iterator[chunk]` — observes chunks, may call services, may
  inject chunks (e.g. a `[Validation]` line), passes the rest through unchanged.
- `ctx` carries `interpreter`, `backend`, and per-turn scratch (snapshots, changed files).
- **Ordering is preserved**: middleware only *observes* and *appends*; it never reorders the
  producer's chunks. The golden oi sequence stays identical when middleware is a pass-through.

## The oi double-execution decision (the central design choice)

`respond()` already calls the services inline. If a middleware also calls them for the oi path we
double-record / double-validate. Two options:

- **(A) Hermes-gated middleware (recommended first delivery).** Middleware runs its service calls
  only when `backend == "hermes"`; oi path is byte-for-byte unchanged (pass-through). Ships the
  north-star payoff fast, zero oi risk. Downside: not yet "one backend-agnostic layer" — oi keeps
  its inline calls, hermes gets equivalent calls via middleware.
- **(B) Backend-agnostic single source (later consolidation).** Move the inline service calls out
  of `respond()` into the shared middleware so *both* backends go through one path. Elegant end
  state, but must reproduce oi's exact chunk/event ordering (the golden suite is the gate) and is a
  bigger change. Defer to a final phase, only if (A) proves the contract.

This scope plans **(A) first**, then **(B)** as an explicit, optional consolidation phase.

## Phased plan (each phase = one commit, golden + hermes tests green, rollback = git)

- **Phase 0 — Seam.** Add `ChunkPipeline` + `Middleware` base; wire a **no-op pass-through** at
  `_respond_and_store` for both backends. Zero behavior change. *Gate: full suite unchanged.*
- **Phase 1 — SystemMessageBuilder → hermes.** In `_drive`, build
  `SystemMessageBuilder().build(interpreter)` and inject it into the ACP session (via `new_session`
  system-prompt param if ACP supports it, else prepended to the first `prompt`). oi untouched.
  *Gate: hermes session carries OI's prompt (assert in `test_hermes_backend`).* **Not a middleware
  — a direct call at the hermes seam; listed here because it's part of "hermes reuses services."**
- **Phase 2 — FileChangeDetector service.** Extract file-change *detection* (snapshot capture/diff,
  from `respond.py:_capture_file_snapshots_before` + the detection half of
  `_detect_file_changes_and_commit`) into a reusable `FileChangeDetector` (co-located with
  MemoryRecorder). oi calls it where it inlines detection now (behavior-identical); the hermes
  MemoryMiddleware needs it to detect edits around the out-of-process turn. *Gate: golden unchanged.*
- **Phase 3 — MemoryMiddleware (hermes).** Snapshot cwd at turn start; on turn end, diff via
  `FileChangeDetector`, then call `MemoryRecorder.record_file_changes` + `commit_edits`. **North-star
  payoff: hermes edits land in SemanticEditGraph and light up MEMORY_RECORD/GIT_COMMIT/FILE_CHANGE.**
  *Gate: a hermes turn that writes a file → row in graph.db + events on the bus.*
- **Phase 4 — ValidationMiddleware (hermes, post-hoc).** After a hermes edit, run `CodeGate`-style
  validation on the changed file contents; on failure either **warn-only** (inject a `[Validation]`
  chunk) or **git-rollback** the edit. Start warn-only (kill criterion: rollback too disruptive to
  hermes UX). *Gate: hermes bad edit → `[Validation]` chunk (+ optional rollback).*
- **Phase 5 — (optional) Consolidate to backend-agnostic (Option B).** Move oi's inline service
  calls into the shared middleware so both backends use one path; delete the now-duplicated inline
  calls from `respond()`. *Gate: golden oi sequence identical; hermes unchanged.* Only attempt if
  Phases 0–4 are stable and the team wants the single-source end state.

**Not in scope:** agent orchestration over hermes (HERMES plan Phase 6 — separate re-entrancy
design); runtime tracing for hermes (oi-only, `sys.settrace` can't cross the process boundary —
document N/A, do not fake).

## Risks & kill criteria

- **Regression gate:** every phase must keep the oi golden sequence byte-identical. If any oi test
  changes behavior in Phases 0–4, stop (those phases are hermes-only + additive services).
- **File detection timing for hermes:** snapshots bracket the *whole* turn (coarser than oi's
  per-cell diff). Acceptable for memory; note the granularity difference.
- **Validation rollback UX:** if post-hoc git rollback surprises users mid-hermes-turn, downgrade to
  warn-only (surface the invalid diff, don't revert).
- **ACP system-prompt support:** if `new_session` has no system-prompt slot, fall back to prepending
  to the first user prompt; verify hermes actually honors it before claiming Phase 1 done.

## Critical files

- `interpreter/core/core.py` — `_respond_and_store` seam (`:916–921`); add `chunk_pipeline`.
- `interpreter/core/backends/hermes_backend.py` — `_drive` (`:323`, system-message inject at `:370`);
  turn-boundary hooks for memory/validation middleware.
- `interpreter/core/services/system_message_builder.py`, `interpreter/core/memory/recorder.py`,
  `interpreter/core/validation/code_gate.py` — the services to reuse (done).
- `interpreter/core/observability.py` — no change (already backend-agnostic; consumes the new events).
- **New:** `interpreter/core/pipeline/` (`ChunkPipeline`, `Middleware`, `Observability/Memory/Validation`
  middleware); `interpreter/core/memory/file_change_detector.py` (Phase 2).
- Tests: extend `tests/core/test_hermes_backend.py`; new `tests/core/pipeline/`; golden stays the oi gate.

## Effort estimate

- Phase 0: ~0.5 day (seam + base + no-op, mechanical).
- Phase 1: ~0.5 day (hermes system-message inject + ACP verification).
- Phase 2: ~0.5 day (FileChangeDetector extraction, behavior-identical for oi).
- Phase 3: ~1 day (MemoryMiddleware + turn-boundary snapshots + graph.db integration test).
- Phase 4: ~1 day (post-hoc validation + warn/rollback decision).
- Phase 5 (optional): ~1–2 days (consolidation, highest oi-regression risk).

**Minimum north-star delivery = Phases 0–3** (hermes runs feed memory + observability): ~2.5 days.
Phases 4–5 are follow-ons.
