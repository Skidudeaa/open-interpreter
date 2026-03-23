# cc-sidecar + Sinter Integration Review

## Why it fits

Sinter's evolution pipeline emits JSON blobs to stdout and runs silently for minutes. cc-sidecar is a passive event store with a TUI dashboard. The mapping is direct:

| Sinter concept | cc-sidecar model |
|---|---|
| Evolution step (10 phases) | Session |
| Ironworks task (select, eval, propose, build...) | Agent activity timeline |
| Parent program + candidate | Agent pair (parent = main, candidate = subagent) |
| Phase success/failure | Tool call lifecycle (started → success/failure) |
| Silent failure (wrong python, no API key) | Alert (severity=error, kind=preflight) |
| Frontier admission/rejection | Activity record with outcome |
| Score delta | Session metric (maps to context_used_pct or custom field) |

## Integration surface

cc-sidecar ingests events via **stdin JSON pipes**. Sinter already emits JSON. The wiring is one line per emit point.

### From Sinter's JS control plane

At each phase boundary in `evolve-step.mjs`:

```js
const { execSync } = require('child_process');

function emitSidecar(eventName, payload) {
  const envelope = JSON.stringify({
    event_name: eventName,
    session_id: stepId,        // evolution step ID
    payload
  });
  try {
    execSync('cc-sidecar emit', { input: envelope, timeout: 1000 });
  } catch { /* never blocks evolution */ }
}

// Phase boundaries
emitSidecar('PhaseStart', { phase: 'select_parent', step: 3 });
emitSidecar('PhaseComplete', { phase: 'select_parent', program: 'prog-0042' });
emitSidecar('PhaseStart', { phase: 'eval_train', batch_size: 5 });
emitSidecar('ScoreUpdate', { program: 'prog-0043', score: 0.72, delta: +0.03 });
emitSidecar('FrontierAdmit', { program: 'prog-0043', rank: 2 });
emitSidecar('FrontierReject', { program: 'prog-0043', reason: 'below_threshold' });
```

### From Sinter's Python domain layer

In `eval_runner.py`, `proposer.py`, `build_candidate.py`:

```python
import subprocess, json

def emit_sidecar(event_name: str, payload: dict, session_id: str) -> None:
    envelope = json.dumps({
        "event_name": event_name,
        "session_id": session_id,
        "payload": payload,
    })
    try:
        subprocess.run(
            ["cc-sidecar", "emit"],
            input=envelope.encode(),
            timeout=1,
            capture_output=True,
        )
    except Exception:
        pass  # never blocks eval

# Usage
emit_sidecar("EvalItem", {"case": 3, "score": 0.8, "hard_failures": 0}, step_id)
emit_sidecar("ProposalEmit", {"failure_class": "missing_assessment", "action": "create"}, step_id)
emit_sidecar("ReviewDeny", {"reason": "executable_file", "path": "run.sh"}, step_id)
```

## What Sinter gets for free

1. **Phase-level progress** — the TUI shows `[eval_train: running 2.3s] [propose: pending]` in the agent strip. Directly closes the "runs silently for minutes" gap.

2. **Failure surfacing** — emit a `PreflightFail` event when `ANTHROPIC_API_KEY` is missing or `EVOSKILL_PYTHON_BIN` is wrong. The sidecar stores it as an alert with severity=error. The TUI renders it red. No more silent `no_change` loops.

3. **Score timeline** — every `ScoreUpdate` event lands in the activity table. The TUI timeline panel shows `Step 3: admitted, score 0.72 (+0.03)` — exactly the human-readable output the gap analysis wants.

4. **Stuck detection** — if a phase runs longer than 120s, the sidecar's health check marks it `blocked` and creates an alert. Covers the case where Python eval hangs on a bad domain.

5. **Post-mortem replay** — all events are immutable in SQLite. After a failed evolution run, `cc-sidecar tui` shows the full timeline: which phase failed, what the score was before failure, which program was the parent.

6. **Claude Code observability** — if you're using Claude Code to develop Sinter, `cc-sidecar install` wires hooks so your coding sessions are tracked alongside evolution runs in the same dashboard.

## What cc-sidecar does NOT cover

- **Config management** — sinter.config.toml is Sinter's problem. The sidecar observes, it doesn't configure.
- **Sinter CLI UX** — `--help`, `sinter init`, `sinter check-env` are Sinter surface work. The sidecar runs alongside, not instead.
- **Domain creation** — the sidecar doesn't know about NoteDomain or skill effects. It just stores whatever JSON you emit.
- **Export/promote** — `sinter export` and `sinter promote` are control-plane actions. The sidecar is read-only.

## Reducer mapping

To make the sidecar's existing reducer useful without modification, map Sinter concepts to the existing event vocabulary:

| Sinter event | Emit as | Reducer handles it as |
|---|---|---|
| Step start | `SessionStart` | Creates session + main agent |
| Phase start | `SubagentStart` with `agent_type=<phase>` | Creates agent in `idle` state |
| Phase complete | `SubagentStop` with summary | Transitions agent to `finished` |
| Eval item scored | `eventbus.ACTIVITY` with `activity_type=execute` | Appends to activity timeline |
| Proposal emitted | `eventbus.ACTIVITY` with `activity_type=plan` | Appends to activity timeline |
| Build staged | `eventbus.FILE_CHANGE` | Tracks file in files table |
| Review deny | `Notification` with severity=warn | Creates alert |
| Preflight fail | `Notification` with severity=error | Creates alert |
| Score update | `statusline` with `context_used_pct=score*100` | Updates session metrics |
| Frontier admit | `eventbus.AGENT_COMPLETE` | Transitions agent to finished |

This means **zero sidecar code changes**. Sinter adapts its emit calls to use event names the reducer already understands.

## Install

```bash
# In your Sinter project
pip install "cc-sidecar @ git+https://github.com/Skidudeaa/open-interpreter.git@claude/observability-sidecar-AvkQ3#subdirectory=cc-sidecar"

# Start daemon (in a separate terminal)
cc-sidecar daemon

# Watch evolution in real time
cc-sidecar tui
```
