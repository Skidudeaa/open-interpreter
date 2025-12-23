1) Answer

Lock the architecture first (or you’ll build a flickering dumpster fire)

Hard rule: one thing “owns” the terminal screen at a time. If you try to have Rich Live do full-screen-ish redraws and have prompt_toolkit also managing input/redraw, you’ll get torn frames, broken cursor state, and random garbage on Windows terminals.

So pick a screen owner:
	•	Primary recommendation: prompt_toolkit owns the screen + event loop, and Rich becomes a renderer (Rich renderables → ANSI text → prompt_toolkit display).
prompt_toolkit is explicitly built for full-screen TUIs, multiline editing, key bindings, completion, etc.  ￼
Rich is great at generating ANSI + layout primitives and can capture output as strings.  ￼
	•	Keep current Rich-only path as a fallback backend when stdin/stdout aren’t TTY (pipes, CI logs) or when user disables TUI.

Kill the “Progressive Textual inside Rich” idea

Textual is legit and mature, but it also wants to be the app framework (it owns screen, input, layout).  ￼
Trying to embed Textual “widgets” inside a Rich streaming UI is not incremental—it’s a rewrite with extra suffering.

If you want Textual later, do it cleanly as a separate UI backend (flag --tui=textual), not a hybrid widget graft.

Library decisions (based on real repos, not vibes)
	•	prompt_toolkit (core input + keybindings + layout) – cross-platform, full-screen capable, multiline, completion, vi/emacs.  ￼
	•	Drop rich_interactive for now. The repo itself says the “future plans” demo is PoC and “not production ready”, and it’s tiny.  ￼
If you already have prompt_toolkit key handling, you don’t need it anyway.
	•	rich-diff optional for diff rendering (tiny project, fine as a starting point).  ￼
	•	Inline images (optional / late):
	•	rich-pixels = portable “looks decent everywhere” via unicode blocks.  ￼
	•	textual-image = uses Kitty TGP / Sixel with unicode fallback.  ￼
	•	term-image = supports Kitty/iTerm image protocols; Windows support is limited.  ￼

Refined phase plan (same priorities, cleaner execution)

Phase 0 (new): UI backend + state model (1–2 PRs, small but mandatory)
Goal: make future phases not a spaghetti death spiral.

Deliverables:
	•	TerminalUIBackend interface
	•	start(), stop(), emit(event), set_mode(mode), invalidate()
	•	UIState dataclasses (single source of truth)
	•	mode, panels visibility, agent list, selected agent, conversation buffer index, metrics, etc.
	•	Event bus / queue
	•	interpreter emits UIEvents (message chunk, tool start, tool end, agent spawn, agent update, error)
	•	UI consumes events, updates UIState, triggers redraw
	•	Two backends
	•	RichStreamBackend = your existing behavior (safe for piping)
	•	PromptToolkitBackend = new interactive TUI

Why this matters: it de-risks everything else and gives you rollback-by-flag.

Phase 1: Input + key bindings (foundation, but do it as an always-on app)
Your current plan says “replace input() with prompt_toolkit session”. That’s not enough if you want Esc to cancel while the model is streaming.

Refine the goal:
	•	Run a prompt_toolkit Application continuously, even while agents run.
	•	Use prompt_toolkit widgets for:
	•	input buffer (multiline + syntax highlight via lexer)  ￼
	•	optional search toolbar (for history search)  ￼
	•	completion menu + fuzzy completer (for palette-like command selection)  ￼

Rendering strategy (critical detail):
	•	Each frame:
	1.	build Rich renderables for zones (status, conversation, context, agent strip)
	2.	Console.capture() to ANSI string  ￼
	3.	wrap ANSI using prompt_toolkit ANSI(...) (it exists for this)  ￼
	4.	display inside prompt_toolkit windows

Keybindings:
	•	Make them configurable (config file/env) because Alt-* is flaky across terminals.
	•	Always provide fallbacks:
	•	Esc cancel
	•	Ctrl+L clear view
	•	F2/F3/... toggles for panels/mode if Alt doesn’t transmit reliably

Also: if you still need to print from background threads/coros, prompt_toolkit explicitly recommends patch_stdout() to avoid destroying the UI.  ￼
But prefer the event bus → UI redraw route instead of raw prints.

Acceptance criteria:
	•	Multiline input, history, completion, syntax highlighting works.
	•	While streaming, Esc cancels immediately.
	•	No flicker at normal refresh rates (target 10–20fps; 30fps is usually pointless CPU tax).

Phase 2: Agent strip + agent drilldown (your “must have”)
Refine implementation so it stays sane:

Data model:
	•	AgentState: id, parent_id, status enum, started_at, last_update, last_lines (ring), error summary.

UI behavior:
	•	Agent strip only appears when agents_running > 0 (but reserve 1 line so layout doesn’t jump)
	•	Focus model: “current focus = input | agents | conversation | context”
	•	Actions:
	•	Enter open agent details overlay
	•	k kill agent (confirm if destructive)
	•	t toggle tree view

Implementation note:
	•	Don’t build a “tree widget” framework. Just render the tree with Rich Tree and maintain selection index yourself. (It’s a couple dozen lines, not a dependency.)

Context window meter:
	•	Put it in status bar, but treat token count as estimated unless you have exact accounting from your model provider.
	•	If you can’t get logprobs/token usage, show “approx chars” instead of lying.

Phase 3: Context side panel + code interaction
Refine the feature set to things terminals can do reliably without mouse fantasy.

Context panel:
	•	Variables/functions:
	•	Only from the actual interpreter execution env (not scraped from text)
	•	Truncate values, show type, provide “inspect” action to expand
	•	Metrics:
	•	time + memory
	•	make psutil optional dependency (extra installs annoy people)
	•	Toggle keys + mode gating, no click/hover.

Code block folds:
	•	Define a fold state per block: collapsed/expanded
	•	Add selection navigation to move between blocks, then toggle fold with Space or Enter

Diff view:
	•	Either integrate rich-diff early for fast win  ￼
or generate unified diffs via difflib and syntax highlight as diff.

Phase 4: Adaptive mode system (make it deterministic, not “magic”)
Your trigger list is fine; the implementation needs guardrails:
	•	Use a score + hysteresis:
	•	score rises with events (agent spawn, error, long run, N exchanges)
	•	only escalate when crossing thresholds
	•	only de-escalate on explicit command (otherwise users rage at UI shrinking)
	•	Make mode changes explainable:
	•	When auto-escalating, show a 1-line toast: “Mode → Standard (agent running)”
	•	Users should never wonder “why did it change”

Implementation:
	•	UIModeManager pure logic (unit-testable)
	•	UI components declare min_mode
	•	prompt_toolkit can conditionally show containers; leverage conditional containers/filters rather than littering if everywhere.  ￼

Phase 5: Polish + experimental toys (keep them quarantined)
	•	Confidence shading: terminals don’t have opacity. You can approximate with dim/color ramps, but only if you have logprobs. Most APIs won’t give you that reliably → default off.
	•	“Thinking breadcrumbs”: don’t expose raw chain-of-thought. Show:
	•	tool calls
	•	planned steps summary
	•	file/section targets
That’s useful and doesn’t become a liability.
	•	Ghost previews: show diffs of proposed edits before apply; that’s real value.
	•	Inline images: add behind capability detection + optional deps:
	•	rich-pixels first (portable)  ￼
	•	textual-image/term-image later for terminals that support protocols  ￼

Security refinement (non-optional for an AI terminal app)

LLM output can contain terminal control sequences that mess with display, clipboard, hyperlinks, etc. That’s not paranoia; it’s a real attack class.  ￼

Add to Phase 0/1:
	•	sanitize output before rendering:
	•	allowlist basic SGR color codes if you want (or strip all escape sequences from model output)
	•	strip OSC sequences (clipboard/hyperlink) by default
	•	Keep “raw output” view behind a debug toggle.

⸻

2) NOTES
	•	rich_interactive is a trap right now. The author literally labels key parts PoC/not production ready, and it’s tiny.  ￼
	•	If you depend on patch_stdout(), test ANSI-heavy output. There’s a long history of edge cases with patched stdout + ANSI streams; don’t assume it’s flawless.  ￼
	•	Alt-key bindings are not portable. You need fallbacks (function keys or ctrl chords) or you’ll get bug reports from every terminal/OS combo.
	•	Re-render cost will bite you. Don’t rebuild the entire conversation as one giant Rich renderable every token. Keep a ring buffer + incremental append.
	•	Mouse “click/hover” features are mostly bullshit in real terminals. Key-driven selection beats fragile mouse support.

⸻

3) NEXT
	1.	Implement Phase 0 backend split + UIState + UIEvent bus. Without this, every phase becomes an irreversible refactor.
	2.	Build prompt_toolkit “always-on” app skeleton: conversation window + input TextArea + global keybindings (Esc cancel).
	3.	Wire streaming into UI via events (no direct prints). Rate-limit redraw; measure CPU.
	4.	Agent strip (Phase 2) immediately after skeleton—it’s the first real “agent-aware” win.
	5.	Add escape-sequence sanitization before you ship the interactive UI to anyone who might paste untrusted text.

If you want, I can turn this into a concrete PR checklist with module/file layout and exact class/function signatures (so the team can implement without bikeshedding).
