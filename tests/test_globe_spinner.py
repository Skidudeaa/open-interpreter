"""
Tests for the Globe Spinner — terminal port of `Globe Loader v2.html`.

These exercise the ported state machine (eased completion, phase fractions,
frame selection) and the integration contract (ThinkingSpinner factory,
fallbacks) without needing a live terminal.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from interpreter.terminal_interface.components.globe_spinner import (  # noqa: E402
    CHECK,
    GLOBE_ASCII,
    GLOBE_EMOJI,
    GlobeSpinner,
    _clamp,
    play_completion_flourish,
)


def _advance(sp, seconds, steps=60):
    """Drive the state machine for ~`seconds` by feeding a fake monotonic clock."""
    import interpreter.terminal_interface.components.globe_spinner as mod

    real = mod.time.monotonic
    t = [0.0]
    mod.time.monotonic = lambda: t[0]
    try:
        sp._last = None
        sp._advance()  # primes the clock at t=0 (no dt applied)
        dt = seconds / steps
        for _ in range(steps):
            t[0] += dt
            sp._advance()
    finally:
        mod.time.monotonic = real


def test_clamp():
    assert _clamp(-1, 0, 1) == 0
    assert _clamp(2, 0, 1) == 1
    assert _clamp(0.5, 0, 1) == 0.5


def test_defaults_match_v2():
    """Defaults are lifted from Globe Loader v2 TWEAK_DEFAULTS."""
    sp = GlobeSpinner()
    assert sp.P["spin_speed"] == 21.0
    assert sp.P["spin_mode"] == "settle"
    assert sp.P["whirl"] == 0.92
    assert sp.P["arms"] == 3
    assert sp.P["ink"] == "#4D3B31"


def test_completion_eases_to_one():
    """state=complete drives comp 0 -> 1 (globe.js comp easing)."""
    sp = GlobeSpinner()
    assert sp.comp == 0.0
    sp.set_params(state="complete")
    _advance(sp, 2.0)
    assert sp.comp > 0.99


def test_loading_keeps_comp_zero():
    sp = GlobeSpinner()
    _advance(sp, 2.0)
    assert sp.comp < 0.01


def test_phase_ordering_whirl_then_ring_then_check():
    """The completion phases activate in the design's order."""
    sp = GlobeSpinner()
    sp.set_params(state="complete")

    # Early: whirl still visible, no check yet. Sample before comp reaches the
    # whirl-fadeout point (comp < 0.4) so whirl_alpha is genuinely positive.
    _advance(sp, 0.05, steps=10)
    early = sp.comp
    assert early < 0.4
    # whirl_alpha = 1 - clamp(comp/0.4) -> still > 0 while comp small
    assert (1 - _clamp(early / 0.4, 0, 1)) > 0
    # check_frac = clamp((comp-0.68)/0.32) -> 0 this early
    assert _clamp((early - 0.68) / 0.32, 0, 1) == 0.0

    # Late: check fully drawn.
    _advance(sp, 2.0)
    assert _clamp((sp.comp - 0.68) / 0.32, 0, 1) >= 0.999


def test_render_shows_globe_while_loading():
    sp = GlobeSpinner()
    _advance(sp, 0.5)
    plain = sp._frame_text().plain
    globe_glyphs = GLOBE_EMOJI + GLOBE_ASCII
    assert any(g in plain for g in globe_glyphs)
    assert "Thinking" in plain
    assert CHECK not in plain  # no checkmark while loading


def test_render_shows_check_when_complete():
    sp = GlobeSpinner()
    sp.set_params(state="complete")
    _advance(sp, 2.0)
    assert CHECK in sp._frame_text().plain


def test_reduce_motion_snaps_completion(monkeypatch):
    monkeypatch.setenv("OI_REDUCE_MOTION", "1")
    sp = GlobeSpinner()
    sp.set_params(state="complete")
    # A single advance should snap comp to target (no animation).
    _advance(sp, 0.05, steps=2)
    assert sp.comp == 1.0


def test_ascii_fallback(monkeypatch):
    monkeypatch.setenv("OI_SPINNER_ASCII", "1")
    sp = GlobeSpinner()
    assert sp._globe_frames == GLOBE_ASCII


def test_settle_replays_ease_in():
    sp = GlobeSpinner(spin_mode="constant")
    sp._spin_cur = 21.0
    sp.set_params(spin_mode="settle")
    assert sp._spin_cur == 0.0  # ease-in replayed


def test_thinking_spinner_returns_globe_by_default(monkeypatch):
    monkeypatch.delenv("OI_SPINNER", raising=False)
    from interpreter.terminal_interface.components.spinner_block import ThinkingSpinner

    sp = ThinkingSpinner()
    assert isinstance(sp, GlobeSpinner)


def test_thinking_spinner_classic_opt_out(monkeypatch):
    monkeypatch.setenv("OI_SPINNER", "classic")
    from interpreter.terminal_interface.components.spinner_block import (
        SpinnerBlock,
        ThinkingSpinner,
    )

    sp = ThinkingSpinner()
    assert isinstance(sp, SpinnerBlock)
    assert not isinstance(sp, GlobeSpinner)


def test_lifecycle_start_stop_idempotent():
    """start()/stop() must not raise even without a real TTY; double stop is safe."""
    sp = GlobeSpinner()
    sp.start()
    sp.stop()
    sp.stop()  # no-op, must not raise
    assert sp.is_active is False


def test_empty_text_renders_glyph_only():
    """The end-of-turn flourish uses text='' — render just the glyph, no ellipsis."""
    sp = GlobeSpinner(text="")
    _advance(sp, 0.5)
    plain = sp._frame_text().plain
    assert "…" not in plain
    assert plain.strip() != ""  # still shows a glyph


def test_complete_lands_checkmark(monkeypatch):
    """complete() must end with comp==1 so the checkmark is fully drawn."""
    sp = GlobeSpinner()
    sp.start()
    # Avoid real sleeping/Live latency in the test.
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    sp.complete(duration=0.0, hold=0.0)
    assert sp.comp == 1.0
    assert sp.is_active is False  # complete() stops the spinner


def test_flourish_noop_when_classic(monkeypatch):
    monkeypatch.setenv("OI_SPINNER", "classic")
    assert play_completion_flourish() is False


def test_flourish_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("OI_SPINNER", raising=False)
    monkeypatch.setenv("OI_GLOBE_COMPLETE", "0")
    assert play_completion_flourish() is False


def test_flourish_noop_when_reduce_motion(monkeypatch):
    monkeypatch.delenv("OI_SPINNER", raising=False)
    monkeypatch.delenv("OI_GLOBE_COMPLETE", raising=False)
    monkeypatch.setenv("OI_REDUCE_MOTION", "1")
    assert play_completion_flourish() is False


def test_flourish_noop_without_tty(monkeypatch):
    """No styled TTY (capture mode) → Live can't start → flourish bails cleanly."""
    monkeypatch.delenv("OI_SPINNER", raising=False)
    monkeypatch.delenv("OI_GLOBE_COMPLETE", raising=False)
    monkeypatch.delenv("OI_REDUCE_MOTION", raising=False)
    from rich.console import Console

    # A non-terminal console makes Rich Live a no-op; helper must not raise.
    result = play_completion_flourish(console=Console(force_terminal=False))
    assert result in (False, True)  # never raises; typically False off-TTY
