"""
Globe Spinner — terminal port of the "Globe Loader v2" design.

This is a faithful *spirit* port (not pixel-for-pixel — a terminal can't draw a
canvas globe) of the Claude Design handoff `Globe Loader v2.html` / `globe.js`.
It reproduces the loader's behaviour rather than its geometry:

    loading:    a rotating globe glyph with an eased ("settle") spin-up and a
                faint whirl/vortex shimmer beside it
    completion: the whirl fades → the globe settles → a progress ring sweeps →
                a checkmark draws on  (same phase ordering as globe.js)

The original renders to a <canvas> with d3 + topojson; here each conceptual
element maps to a Unicode glyph sequence on a single line. The state machine
(eased spin, completion easing, phase fractions) is ported directly from
`globe.js tick()/render()` so the timing feels the same.

Palette: the v2 default is "Dark Roast" — warm brown ink (#4D3B31) on warm
paper. We carry the ink hex through to the Rich style so the loader keeps its
identity in a styled terminal.

Public API mirrors ``SpinnerBlock`` (start/update/stop) so it is a drop-in for
``ThinkingSpinner``; it adds ``set_params()`` and ``complete()`` for callers
that want the completion flourish.

Environment:
    OI_SPINNER=globe|classic   selects globe vs. the legacy dots spinner
    OI_SPINNER_ASCII=1         force ASCII globe frames (no emoji)
    OI_REDUCE_MOTION=1         honour reduced-motion: static glyph, no spin
    OI_GLOBE_COMPLETE=0        disable the end-of-turn completion flourish
"""

from __future__ import annotations

import logging
import os
import time

from rich.console import Console
from rich.text import Text

from .theme import THEME

logger = logging.getLogger(__name__)


def _clamp(v: float, a: float, b: float) -> float:
    return max(a, min(b, v))


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _supports_emoji() -> bool:
    """Best-effort: emoji globe frames need a UTF-8 stdout and no opt-out.

    The project already relies on emoji elsewhere (role/language icons), so we
    default to emoji and only fall back when explicitly forced or when stdout is
    plainly not UTF-8.
    """
    if _env_flag("OI_SPINNER_ASCII"):
        return False
    enc = (getattr(__import__("sys").stdout, "encoding", "") or "").lower()
    return "utf" in enc


# v2 defaults, lifted from Globe Loader v2.html TWEAK_DEFAULTS.
# spinSpeed 21, spin "Ease-in" (settle), whirl 0.92, arms 3, ink "#4D3B31",
# style "Fine", state "Loading".
DEFAULT_PARAMS = {
    "spin_speed": 21.0,  # °/s in the original; here drives frame cadence
    "spin_mode": "settle",  # "settle" (ease-in) | "constant"
    "whirl": 0.92,  # 0..1 vortex intensity
    "arms": 3,
    "ink": "#4D3B31",  # Dark Roast ink
    "style": "fine",  # outline | filled | fine (affects ring density)
    "state": "loading",  # loading | complete
}

# Rotating-globe frames. Emoji trio reads unmistakably as a spinning earth —
# the closest single-cell analog to the orthographic rotation. ASCII fallback
# uses rotating half-discs which still read as a turning sphere.
GLOBE_EMOJI = ["\U0001f30d", "\U0001f30e", "\U0001f30f"]  # 🌍 🌎 🌏
GLOBE_ASCII = ["◐", "◓", "◑", "◒"]  # ◐ ◓ ◑ ◒

# Whirl/vortex shimmer — braille frames that read as a swirl orbiting the globe.
WHIRL_FRAMES = ["⠇", "⠦", "⠴", "⠲", "⠒", "⠋", "⠙", "⠸"]

# Progress-ring sweep (the design draws an arc that grows toward full circle).
RING_FRAMES = ["◜", "◝", "◞", "◟"]  # ◜ ◝ ◞ ◟

CHECK = "✓"  # ✓ — completion checkmark (drawn on in globe.js drawCheck)

# Frame-cadence gains: decouple the discrete glyph cadence from the original's
# literal degrees-per-second so the default spin_speed (21) yields a pleasant
# ~6 fps rotation rather than one glyph change every ~6 s.
_SPIN_GAIN = 0.30
_WHIRL_GAIN = 0.85


class GlobeSpinner:
    """Loading spinner that renders the Globe Loader v2 behaviour in a terminal.

    Drive it with Rich ``Live``; ``__rich__`` advances the ported state machine
    off a monotonic clock and returns a fresh ``Text`` each refresh — the same
    pattern Rich's own ``Spinner`` uses.
    """

    def __init__(
        self,
        console: Console | None = None,
        *,
        text: str = "Thinking",
        refresh_per_second: int = 15,
        **params,
    ):
        self.console = console or Console()
        self.text = text
        self.refresh_per_second = refresh_per_second

        self.P = dict(DEFAULT_PARAMS)
        self.P.update(params)

        self._emoji = _supports_emoji()
        self._reduce_motion = _env_flag("OI_REDUCE_MOTION")
        self._globe_frames = GLOBE_EMOJI if self._emoji else GLOBE_ASCII

        # --- ported state (see globe.js) ---
        self._anim = 0.0  # globe rotation accumulator (longitude analog)
        self._whirl_anim = 0.0  # vortex angle accumulator
        self._spin_cur = 0.0  # eased current spin speed
        self._comp = 0.0  # completion progress 0..1
        self._last = None  # monotonic timestamp of previous tick
        self._render_count = 0  # frames drawn; drives the ring sweep

        self.live = None
        self.is_active = False

    # -- parameters ---------------------------------------------------------
    def set_params(self, **np) -> None:
        """Update params live. ``spin_mode`` → 'settle' replays the ease-in."""
        to_settle = np.get("spin_mode") == "settle" and self.P["spin_mode"] != "settle"
        self.P.update(np)
        if to_settle:
            self._spin_cur = 0.0  # replay ease-in, mirrors globe.js modeToSettle

    @property
    def comp(self) -> float:
        return self._comp

    # -- ported tick(): advance the state machine by dt ---------------------
    def _advance(self) -> None:
        now = time.monotonic()
        if self._last is None:
            self._last = now
            if self.P["spin_mode"] != "settle":
                self._spin_cur = self.P["spin_speed"]
            return
        dt = min(now - self._last, 0.08)  # clamp like globe.js (hidden-tab guard)
        self._last = now

        # completion easing toward target (globe.js: comp += (t-comp)*min(1,dt*4.5))
        target = 1.0 if self.P["state"] == "complete" else 0.0
        if self._reduce_motion:
            self._comp = target  # no animation: snap
        else:
            self._comp += (target - self._comp) * min(1.0, dt * 4.5)
            if abs(target - self._comp) < 0.002:
                self._comp = target

        # spin: eased ("settle") or constant, decelerating as it completes
        decel = 1.0 - _clamp(self._comp / 0.5, 0.0, 1.0)
        target_speed = float(self.P["spin_speed"])
        if self.P["spin_mode"] == "settle":
            self._spin_cur += (target_speed - self._spin_cur) * min(1.0, dt * 1.4)
        else:
            self._spin_cur = target_speed

        if not self._reduce_motion:
            self._anim += dt * self._spin_cur * decel * _SPIN_GAIN
            self._whirl_anim += dt * (1.4 + 1.0 * float(self.P["whirl"])) * _WHIRL_GAIN

    # -- ported render(): map state → a single line of glyphs ---------------
    def _frame_text(self) -> Text:
        comp = self._comp
        # Phase fractions — identical constants to globe.js render().
        whirl_alpha = 1.0 - _clamp(comp / 0.4, 0.0, 1.0)
        ring_frac = _clamp((comp - 0.28) / 0.42, 0.0, 1.0)
        check_frac = _clamp((comp - 0.68) / 0.32, 0.0, 1.0)

        ink = self.P["ink"]
        out = Text()

        # whirl/vortex shimmer — only while it hasn't faded and intensity is real
        show_whirl = (
            not self._reduce_motion
            and float(self.P["whirl"]) > 0.05
            and whirl_alpha > 0.2
        )
        if show_whirl:
            wf = WHIRL_FRAMES[int(self._whirl_anim) % len(WHIRL_FRAMES)]
            out.append(wf + " ", style=f"dim {ink}")

        # lead glyph: globe (loading) → ring sweep (progress) → check (done)
        if check_frac >= 0.5:
            lead, lead_style = CHECK, ink
        elif ring_frac > 0.0 and check_frac < 0.5:
            # Sweep the ring off the per-frame render count so it rotates even in
            # the brief end-of-turn flourish, where _whirl_anim barely advances.
            rf = RING_FRAMES[self._render_count % len(RING_FRAMES)]
            lead, lead_style = rf, ink
        else:
            gi = int(self._anim) % len(self._globe_frames)
            lead, lead_style = self._globe_frames[gi], ink
        out.append(lead, style=lead_style)
        if self.text:
            out.append("  ")
            out.append(f"{self.text}…", style=ink)
        return out

    def __rich__(self) -> Text:
        self._advance()
        self._render_count += 1
        return self._frame_text()

    # -- lifecycle (SpinnerBlock-compatible) --------------------------------
    def start(self, text: str | None = None) -> None:
        from rich.live import Live  # local import keeps module import cheap

        if text:
            self.text = text
        self._last = None  # reset clock so ease-in replays from this start
        if self.P["spin_mode"] == "settle":
            self._spin_cur = 0.0
        try:
            self.live = Live(
                self,
                console=self.console,
                refresh_per_second=self.refresh_per_second,
                transient=True,
            )
            self.live.start()
            self.is_active = True
        except Exception as e:
            logger.debug(f"GlobeSpinner failed to start: {e}")
            self.is_active = False
            if self.live:
                try:
                    self.live.stop()
                except Exception:
                    pass
            self.live = None
            # plain fallback so the user still sees activity
            glyph = self._globe_frames[0]
            self.console.print(f"[{self.P['ink']}]{glyph} {self.text}…[/]")

    def update(self, text: str) -> None:
        self.text = text
        if self.is_active and self.live:
            self.live.update(self)

    def complete(
        self,
        final_message: str | None = None,
        *,
        success: bool = True,
        duration: float = 0.45,
        hold: float = 0.13,
    ) -> None:
        """Play the completion flourish (whirl fade → globe settle → ring → check).

        Mirrors the design's ``replayCompletion`` + ``state: Complete``: eases
        ``comp`` 0→1 over ``duration``, then lands the checkmark and holds it for
        ``hold`` seconds so it actually reads before the transient stop clears it.
        Adds ~``duration + hold`` seconds of latency, so callers on the hot path
        should prefer ``stop()``; use this only where a completion moment is wanted.
        """
        if self.is_active and self.live and not self._reduce_motion:
            self.set_params(state="complete")
            end = time.monotonic() + max(0.0, duration)
            interval = 1.0 / max(1, self.refresh_per_second)
            while time.monotonic() < end and self._comp < 0.999:
                self.live.refresh()
                time.sleep(interval)
            # Land the checkmark (the easing only reaches ~0.9 in a short window)
            # and hold it briefly so the completion gesture registers.
            self._comp = 1.0
            try:
                self.live.refresh()
            except Exception:
                pass
            if hold > 0:
                time.sleep(hold)
        self.stop(final_message, success=success)

    def stop(self, final_message: str | None = None, success: bool = True) -> None:
        if not self.is_active:
            return
        # Clear self.live before stopping so a second stop() is a safe no-op
        # (same guard as SpinnerBlock — both content-arrival and exception paths
        # may call stop()).
        live, self.live = self.live, None
        self.is_active = False
        if live:
            try:
                live.stop()
            except Exception:
                pass

        if final_message:
            if success:
                icon, color = CHECK, THEME["success"]
            else:
                icon, color = "✗", THEME["error"]
            self.console.print(f"[{color}]{icon}[/{color}] {final_message}")


def play_completion_flourish(
    console: Console | None = None,
    *,
    duration: float = 0.45,
    text: str = "",
) -> bool:
    """Play a brief end-of-turn globe completion gesture, then clear.

    A standalone, transient flourish (globe settles → ring sweeps → ✓) that
    leaves no residue in scrollback — the satisfaction is in the motion, not a
    persistent log line. Returns True if it played.

    No-ops (returns False) when the globe spinner is disabled (OI_SPINNER=classic),
    when reduced motion is requested (OI_REDUCE_MOTION), when explicitly turned
    off (OI_GLOBE_COMPLETE in 0/false/no/off), or when stdout is not a styled TTY
    (Rich ``Live`` fails to start) — so pipes/CI stay clean.
    """
    if os.environ.get("OI_SPINNER", "globe").strip().lower() == "classic":
        return False
    if os.environ.get("OI_GLOBE_COMPLETE", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return False
    if _env_flag("OI_REDUCE_MOTION"):
        return False
    try:
        sp = GlobeSpinner(console=console, text=text)
        sp.start()
        if not sp.is_active:  # Live couldn't start (non-tty / capture) — bail clean
            sp.stop()
            return False
        sp.complete(duration=duration)
        return True
    except Exception:
        logger.debug("completion flourish failed", exc_info=True)
        return False


def demo(duration: float = 2.0) -> None:
    """Watch the port: spin for ``duration`` s, then play the completion."""
    sp = GlobeSpinner(text="Thinking")
    sp.start()
    try:
        time.sleep(duration)
        sp.update("Wrapping up")
        sp.complete("Done", duration=0.8)
    except KeyboardInterrupt:
        sp.stop()


if __name__ == "__main__":
    demo()
