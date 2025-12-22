"""
UI Mode Manager - Adaptive mode system with auto-escalation.

Manages automatic transitions between ZEN → STANDARD → POWER → DEBUG
based on activity scoring, with manual override support.

Part of Phase 4: Adaptive Mode System
"""

from dataclasses import dataclass, field
from typing import Optional, Callable, List
from enum import Enum, auto
import time

from .ui_state import UIState, UIMode
from .ui_events import UIEvent, EventType


class EscalationReason(Enum):
    """Reasons for mode escalation"""
    AGENT_SPAWN = auto()
    AGENT_ERROR = auto()
    LONG_EXECUTION = auto()
    MULTIPLE_ERRORS = auto()
    HIGH_TOKEN_USAGE = auto()
    USER_REQUEST = auto()
    CODE_EXECUTION = auto()


@dataclass
class ModeTransition:
    """Record of a mode transition"""
    from_mode: UIMode
    to_mode: UIMode
    reason: EscalationReason
    timestamp: float = field(default_factory=time.time)
    message: str = ""


class UIModeManager:
    """
    Manages UI mode transitions with auto-escalation and hysteresis.

    Modes (ascending complexity):
    - ZEN: Conversation only, minimal UI
    - STANDARD: + Status bar, collapsible outputs
    - POWER: + Context panel, agent strip, metrics
    - DEBUG: + Token counts, timing, raw chunks

    Auto-escalation triggers:
    - Agent spawn: +10 points
    - Code execution: +3 points
    - Error: +5 points
    - Long execution (>5s): +3 points
    - High token usage (>80%): +5 points

    Thresholds:
    - ZEN → STANDARD: 5 points
    - STANDARD → POWER: 15 points
    - POWER → DEBUG: 30 points

    Decay:
    - Score decays by 1 point every 30 seconds of inactivity
    - Mode never auto-downgrades (manual only)
    """

    # Score thresholds for mode escalation
    THRESHOLDS = {
        UIMode.ZEN: 0,
        UIMode.STANDARD: 5,
        UIMode.POWER: 15,
        UIMode.DEBUG: 30,
    }

    # Point values for events
    SCORES = {
        EventType.AGENT_SPAWN: 10,
        EventType.AGENT_ERROR: 5,
        EventType.CONSOLE_OUTPUT: 1,
        EventType.CODE_START: 3,
        EventType.SYSTEM_ERROR: 5,
        EventType.CONSOLE_ERROR: 3,
        EventType.SYSTEM_TOKEN_UPDATE: 0,  # Handled specially
    }

    # Decay rate
    DECAY_INTERVAL = 30.0  # seconds
    DECAY_AMOUNT = 1

    def __init__(self, state: UIState):
        """
        Initialize the mode manager.

        Args:
            state: The UIState instance to manage
        """
        self.state = state
        self._score = 0
        self._last_decay_time = time.time()
        self._locked_mode: Optional[UIMode] = None  # Manual override
        self._history: List[ModeTransition] = []
        self._max_history = 50

        # Callbacks
        self._on_mode_change: Optional[Callable[[UIMode, UIMode, str], None]] = None
        self._on_toast: Optional[Callable[[str], None]] = None

        # Error tracking for escalation
        self._error_count = 0
        self._last_error_time = 0.0

        # Execution tracking
        self._execution_start: Optional[float] = None

    @property
    def score(self) -> int:
        """Get current complexity score (with decay applied)."""
        self._apply_decay()
        return self._score

    @property
    def current_mode(self) -> UIMode:
        """Get current UI mode."""
        return self.state.mode

    @property
    def is_locked(self) -> bool:
        """True if mode is manually locked."""
        return self._locked_mode is not None

    @property
    def locked_mode(self) -> Optional[UIMode]:
        """Get the locked mode, if any."""
        return self._locked_mode

    def process_event(self, event: UIEvent) -> Optional[ModeTransition]:
        """
        Process a UI event and potentially trigger mode escalation.

        Args:
            event: The UIEvent to process

        Returns:
            ModeTransition if mode changed, None otherwise
        """
        # Skip if mode is locked
        if self._locked_mode is not None:
            return None

        # Apply decay first
        self._apply_decay()

        # Calculate score delta based on event type
        delta = self.SCORES.get(event.type, 0)
        reason = None

        # Special handling for specific events
        if event.type == EventType.AGENT_SPAWN:
            reason = EscalationReason.AGENT_SPAWN

        elif event.type == EventType.AGENT_ERROR:
            reason = EscalationReason.AGENT_ERROR
            self._error_count += 1
            self._last_error_time = time.time()
            # Multiple errors in short time = extra escalation
            if self._error_count >= 3 and (time.time() - self._last_error_time) < 60:
                delta += 5
                reason = EscalationReason.MULTIPLE_ERRORS

        elif event.type in (EventType.SYSTEM_ERROR, EventType.CONSOLE_ERROR):
            reason = EscalationReason.AGENT_ERROR
            self._error_count += 1
            self._last_error_time = time.time()

        elif event.type == EventType.CODE_START:
            self._execution_start = time.time()
            reason = EscalationReason.CODE_EXECUTION

        elif event.type == EventType.CODE_END:
            if self._execution_start:
                execution_time = time.time() - self._execution_start
                if execution_time > 5.0:
                    delta += 3
                    reason = EscalationReason.LONG_EXECUTION
                self._execution_start = None

        elif event.type == EventType.SYSTEM_TOKEN_UPDATE:
            # Check for high token usage
            if self.state.context_usage_percent > 80:
                delta += 5
                reason = EscalationReason.HIGH_TOKEN_USAGE

        # Update score
        if delta > 0:
            self._score += delta
            return self._check_escalation(reason)

        return None

    def _apply_decay(self):
        """Apply score decay based on time elapsed."""
        now = time.time()
        elapsed = now - self._last_decay_time

        if elapsed >= self.DECAY_INTERVAL:
            decay_cycles = int(elapsed / self.DECAY_INTERVAL)
            self._score = max(0, self._score - (decay_cycles * self.DECAY_AMOUNT))
            self._last_decay_time = now

            # Also decay error count
            if now - self._last_error_time > 120:  # 2 minutes
                self._error_count = 0

    def _check_escalation(self, reason: Optional[EscalationReason]) -> Optional[ModeTransition]:
        """
        Check if score warrants mode escalation.

        Args:
            reason: The reason for potential escalation

        Returns:
            ModeTransition if mode changed, None otherwise
        """
        current = self.state.mode
        target = current

        # Find appropriate mode for current score
        for mode in [UIMode.DEBUG, UIMode.POWER, UIMode.STANDARD, UIMode.ZEN]:
            if self._score >= self.THRESHOLDS[mode]:
                target = mode
                break

        # Only escalate, never auto-downgrade
        if self._mode_level(target) > self._mode_level(current):
            return self._transition_to(target, reason or EscalationReason.USER_REQUEST)

        return None

    def _mode_level(self, mode: UIMode) -> int:
        """Get numeric level for mode comparison."""
        return {
            UIMode.ZEN: 0,
            UIMode.STANDARD: 1,
            UIMode.POWER: 2,
            UIMode.DEBUG: 3,
        }.get(mode, 0)

    def _transition_to(
        self, target: UIMode, reason: EscalationReason
    ) -> ModeTransition:
        """
        Execute mode transition.

        Args:
            target: Target UIMode
            reason: Reason for transition

        Returns:
            ModeTransition record
        """
        from_mode = self.state.mode
        self.state.mode = target

        # Build message
        reason_messages = {
            EscalationReason.AGENT_SPAWN: "agent running",
            EscalationReason.AGENT_ERROR: "agent error",
            EscalationReason.LONG_EXECUTION: "long execution",
            EscalationReason.MULTIPLE_ERRORS: "multiple errors",
            EscalationReason.HIGH_TOKEN_USAGE: "high context usage",
            EscalationReason.USER_REQUEST: "manual",
            EscalationReason.CODE_EXECUTION: "code execution",
        }
        message = f"Mode → {target.name} ({reason_messages.get(reason, 'auto')})"

        # Create transition record
        transition = ModeTransition(
            from_mode=from_mode,
            to_mode=target,
            reason=reason,
            message=message,
        )

        # Record history
        self._history.append(transition)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        # Notify listeners
        if self._on_mode_change:
            self._on_mode_change(from_mode, target, message)
        if self._on_toast:
            self._on_toast(message)

        return transition

    # Manual mode control

    def set_mode(self, mode: UIMode, lock: bool = False) -> ModeTransition:
        """
        Manually set UI mode.

        Args:
            mode: Target UIMode
            lock: If True, prevent auto-escalation

        Returns:
            ModeTransition record
        """
        if lock:
            self._locked_mode = mode
        else:
            self._locked_mode = None

        # Set score to match mode threshold
        self._score = self.THRESHOLDS[mode]

        return self._transition_to(mode, EscalationReason.USER_REQUEST)

    def lock_mode(self, mode: UIMode):
        """Lock to a specific mode (disable auto-escalation)."""
        self._locked_mode = mode
        self.state.mode = mode

    def unlock_mode(self):
        """Unlock mode (re-enable auto-escalation)."""
        self._locked_mode = None

    def toggle_power_mode(self) -> ModeTransition:
        """Toggle between STANDARD and POWER mode."""
        if self.state.mode == UIMode.POWER:
            return self.set_mode(UIMode.STANDARD)
        else:
            return self.set_mode(UIMode.POWER)

    def cycle_mode(self) -> ModeTransition:
        """Cycle through modes: ZEN → STANDARD → POWER → DEBUG → ZEN."""
        mode_order = [UIMode.ZEN, UIMode.STANDARD, UIMode.POWER, UIMode.DEBUG]
        current_idx = mode_order.index(self.state.mode)
        next_idx = (current_idx + 1) % len(mode_order)
        return self.set_mode(mode_order[next_idx])

    def set_zen(self) -> ModeTransition:
        """Switch to ZEN mode (minimal UI)."""
        return self.set_mode(UIMode.ZEN, lock=True)

    def set_debug(self) -> ModeTransition:
        """Switch to DEBUG mode (maximum detail)."""
        return self.set_mode(UIMode.DEBUG, lock=True)

    # Callbacks

    def set_mode_change_handler(self, handler: Callable[[UIMode, UIMode, str], None]):
        """Set callback for mode changes: (from_mode, to_mode, message)."""
        self._on_mode_change = handler

    def set_toast_handler(self, handler: Callable[[str], None]):
        """Set callback for toast notifications."""
        self._on_toast = handler

    # Query methods

    def get_mode_info(self) -> dict:
        """Get current mode information for display."""
        return {
            "mode": self.state.mode.name,
            "score": self._score,
            "threshold": self.THRESHOLDS[self.state.mode],
            "locked": self._locked_mode is not None,
            "next_threshold": self._get_next_threshold(),
        }

    def _get_next_threshold(self) -> Optional[int]:
        """Get threshold for next mode escalation."""
        levels = [UIMode.ZEN, UIMode.STANDARD, UIMode.POWER, UIMode.DEBUG]
        current_idx = levels.index(self.state.mode)
        if current_idx < len(levels) - 1:
            return self.THRESHOLDS[levels[current_idx + 1]]
        return None

    def get_history(self, limit: int = 10) -> List[ModeTransition]:
        """Get recent mode transition history."""
        return self._history[-limit:]

    def get_status_text(self) -> str:
        """Get status text for display."""
        mode = self.state.mode.name
        if self._locked_mode:
            return f"[{mode}] (locked)"

        next_threshold = self._get_next_threshold()
        if next_threshold:
            return f"[{mode}] {self._score}/{next_threshold}"
        return f"[{mode}]"
