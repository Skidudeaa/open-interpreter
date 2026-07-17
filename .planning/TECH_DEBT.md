# Tech Debt Register

Generated: 2026-05-01 — parallel scan of `interpreter/core/`, `interpreter/terminal_interface/`, `interpreter/sdk/`, `cc-sidecar/`
HIGH tier re-verified against live code: 2026-07-17.

**Totals: 10 HIGH · 56 MED · 25 LOW**  → HIGH now **4 open · 6 resolved/invalid** (see below).

Items are ordered within each tier by blast-radius. Fix HIGHs before touching MED; MED before LOW.

---

## HIGH

> **2026-07-17 re-verification:** The 2026-05-01 scan predated several commits. On
> re-check, the CRASH BUG and SECURITY items were already fixed, and the
> `respond.py` exception finding was a false positive. The two remaining sidecar
> exception blocks were hardened. Only the OVER_COMPLEXITY monoliths remain open.

### ✅ RESOLVED — CRASH BUG
| File | Line | Status |
|------|------|--------|
| `cc-sidecar/src/cc_sidecar/cli.py` | 90 | **Already fixed** (commit `3f44d6b2`). No `_execute` reference exists in the sidecar; line 90 calls the public `store.get_sessions(limit=5)` (defined `db/store.py:222`), wrapped in try/except. Not a runtime crash. |

### ⚠️ OPEN — OVER_COMPLEXITY (untestable monoliths)
These are the only genuinely-open HIGH items. Each is a dedicated refactor (use the `extract` skill), **not** a bugfix pass — do them one at a time with tests after each cut.
| File | Lines | Description |
|------|-------|-------------|
| `interpreter/core/respond.py` | ~1324 | `respond()` mixes agent orchestration, LLM calls, code execution, validation, auto-commit, memory, and tracing — no sub-function decomposition. |
| `interpreter/terminal_interface/terminal_interface.py` | ~1036 | `terminal_interface()` is a single 1036-line event loop handling chat, streaming, display, magic commands, and agent execution. |
| `interpreter/terminal_interface/start_terminal_interface.py` | ~671 | `start_terminal_interface()` handles arg parsing, validation, config loading, and UI init in one function. |
| `interpreter/core/async_core.py` | ~799 | `create_router()` mixes WebSocket, HTTP, and WebRTC routing with server lifecycle — should split into separate handler modules. |

### ✅ RESOLVED — SECURITY
| File | Line | Status |
|------|------|--------|
| `cc-sidecar/src/cc_sidecar/daemon/server.py` | 147 | **Already fixed** (commit `3f44d6b2`) — `mkdir(parents=True, mode=0o700, exist_ok=True)`; socket dir is `~/.cc-sidecar/`. 2026-07-17: added a defensive `chmod(0o700)` to also tighten a pre-existing looser dir (the `exist_ok=True` gap). |

### EXCEPTION_SWALLOWING (high-impact paths)
| File | Line | Status |
|------|------|--------|
| `interpreter/core/respond.py` | – | **Invalid / false positive.** respond.py has no "reducer paths." The bare `except Exception` blocks are intentional non-blocking fallbacks (headless detection `L91`, optional tracing `L1144`) with explanatory comments, or they surface the error (`L1553` yields the traceback). Left as-is. |
| `cc-sidecar/src/cc_sidecar/reducer/state_machine.py` | 72–75 | **Acceptable.** Already logs `event_name` + `session_id` via `logger.exception` (not silent); broad catch is deliberate reducer resilience — one bad event must not crash the daemon. Raw events remain replayable. |
| `cc-sidecar/src/cc_sidecar/daemon/server.py` | 118–119 | **Fixed 2026-07-17.** `process_event` now logs the failing `event_name`/`session_id` (was context-free "Error processing event"), so dropped events are diagnosable and recoverable via raw_events replay. |

---

## MED

### DEBUG_ARTIFACT — print() instead of logger

**interpreter/core/**
| File | Lines |
|------|-------|
| `interpreter/core/render_message.py` | 40, 41, 42 |
| `interpreter/core/core.py` | 877 |
| `interpreter/core/respond.py` | 634, 659, 669, 847, 848, 1311, 1321, 1322, 1378 |
| `interpreter/core/validation/auto_commit.py` | 46 |

**cc-sidecar/**
| File | Lines |
|------|-------|
| `cc-sidecar/src/cc_sidecar/cli.py` | 63–106 (44 print statements) |
| `cc-sidecar/src/cc_sidecar/config/install.py` | 95, 166, 170, 177, 181 |

**sdk/**
| File | Lines |
|------|-------|
| `interpreter/sdk/plugins.py` | 13, 17 |

### EXCEPTION_SWALLOWING — medium-risk paths

**interpreter/core/**
| File | Lines | Context |
|------|-------|---------|
| `interpreter/core/core.py` | 227 | `return False` swallows context, no log |
| `interpreter/core/observability.py` | 174 | `pass` — non-blocking but invisible |
| `interpreter/core/observability.py` | 221 | `return False` swallows socket error |
| `interpreter/core/validation/rollback.py` | 113, 143, 213, 253, 265, 277, 289 | All git/file rollback methods return `False` silently on failure — debugging impossible |

**terminal_interface/sdk/**
| File | Lines | Context |
|------|-------|---------|
| `interpreter/terminal_interface/components/activity_stream.py` | 258, 304 | Silent Rich Live errors |
| `interpreter/terminal_interface/components/base_block.py` | 170 | Silent cleanup failure |
| `interpreter/terminal_interface/components/spinner_block.py` | 82, 116 | Silent Live.stop() errors |
| `interpreter/terminal_interface/components/table_display.py` | 329 | Silent render failure |
| `interpreter/terminal_interface/components/ui_backend.py` | 194 | Silent fallback failure |
| `interpreter/terminal_interface/components/ui_events.py` | 348, 364 | Silent event handler errors |
| `interpreter/terminal_interface/textual_backend.py` | 89 | Silent Textual app exit failure |
| `interpreter/terminal_interface/textual_app.py` | 1052 | Silent loading-indicator removal failure |
| `interpreter/sdk/mcp_bridge.py` | 136, 188 | Network init errors swallowed without distinguishing timeout/connection/HTTP |

**cc-sidecar/**
| File | Lines | Context |
|------|-------|---------|
| `cc-sidecar/src/cc_sidecar/ingest/emit.py` | 133–134 | Hook errors hidden from caller |
| `cc-sidecar/src/cc_sidecar/ingest/statusline.py` | 65–66 | `pass` — completely silent |
| `cc-sidecar/src/cc_sidecar/ingest/transport.py` | 65–70 | Transport send failures silent |
| `cc-sidecar/src/cc_sidecar/daemon/server.py` | 136–138, 173, 197, 219, 307–308, 360–361 | WebSocket broadcast, connection handler, health checks, lock release — all swallow |
| `cc-sidecar/src/cc_sidecar/tui/app.py` | 157, 219, 251, 274 | Four silent exception blocks in TUI WS + DB loading |

### TYPE_HINT_MISSING — public functions

| File | Lines | Missing |
|------|-------|---------|
| `interpreter/core/core.py` | 45, 75, 97, 118 | Return types on `_get_*_module()` helpers |
| `interpreter/core/respond.py` | 69, 107, 213 | `interpreter` param types + return annotations on main functions |

### DUPLICATE_LOGIC

| File | Description |
|------|-------------|
| `interpreter/terminal_interface/components/spinner_block.py` | Reimplements Rich.Live spinner wrapper already in `live_agent_tracker.py` |

---

## LOW

### COMMENTED_CODE — dead blocks to delete

| File | Lines |
|------|-------|
| `interpreter/core/async_core.py` | 321, 1274 |
| `interpreter/core/core.py` | 773, 805 |
| `interpreter/core/respond.py` | 690 |
| `interpreter/terminal_interface/profiles/defaults/os.py` | 221–229, 238–243 |
| `interpreter/terminal_interface/profiles/defaults/codestral-few-shot.py` | 86 |
| `interpreter/terminal_interface/start_terminal_interface.py` | 389 |

### OVER_COMPLEXITY — long methods (not blocking, but should be extracted)

| File | Lines | Notes |
|------|-------|-------|
| `interpreter/terminal_interface/components/pt_app.py` | 449, 194 | `_show_history_search()` 114 lines; `_create_key_bindings()` 83 lines |
| `interpreter/sdk/agent_builder.py` | 160, 731 | `execute()` 83 lines; `create_core_agent()` complex branching |

### UNTESTED_PATH

| Area | What's missing |
|------|----------------|
| `cc-sidecar/` daemon | `_run_socket_listener`, `_handle_connection`, `_run_health_checks`, `_run_ws_server` — zero test coverage |
| `cc-sidecar/` TUI | WS connection, DB fallback, panel rendering — entirely untested |

### COUPLING

| File | Line | Description |
|------|------|-------------|
| `interpreter/sdk/plugins.py` | 323 | SDK imports `EventBus` from `terminal_interface.components.ui_events` — creates SDK→UI dependency |

### BARE_EXCEPT — low-risk paths

| File | Lines |
|------|-------|
| `interpreter/terminal_interface/contributing_conversations.py` | 193 |
| `interpreter/terminal_interface/profiles/profiles.py` | 271 |
| `interpreter/terminal_interface/start_terminal_interface.py` | 597 |
| `interpreter/terminal_interface/terminal_interface.py` | 63, 658 |
| `interpreter/sdk/agent_builder.py` | 66 |

---

## Suggested Sprint Ordering

| Sprint | Items | Rationale |
|--------|-------|-----------|
| 1 | CRASH BUG (`cli.py:90`), SECURITY (`server.py:147`) | Correctness before everything |
| 2 | `rollback.py` exception swallowing (6 methods) | Silent git/file failures are operationally blind |
| 3 | All `print()` → `logger` conversions | Low-risk, high-signal improvement to observability |
| 4 | `respond()` decomposition | Largest single debt item; requires careful extraction |
| 5 | Remaining exception swallowing (MED) | Reduce silent failure surface area |
| 6 | Commented code, type hints, coupling | Hygiene pass |
