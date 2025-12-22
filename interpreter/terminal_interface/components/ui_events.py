"""
UI Event System

Event-driven architecture for interpreter → UI communication.
Replaces direct generator consumption with a proper event bus.

Part of Phase 0: Foundation (must be implemented before other UI phases)

Usage:
    bus = EventBus()

    # Subscribe to events
    bus.subscribe(EventType.MESSAGE_CHUNK, lambda e: print(e.data["content"]))

    # Emit events (from interpreter thread)
    bus.emit(UIEvent(type=EventType.MESSAGE_CHUNK, data={"content": "Hello"}))

    # Process events (in UI thread)
    while event := bus.poll():
        handle(event)
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Dict, List, Optional, Any
from queue import Queue, Empty
from threading import Lock
import time


class EventType(Enum):
    """
    All UI events that can be emitted.

    Grouped by source:
    - MESSAGE_*: From LLM responses
    - CODE_*: From code blocks
    - CONSOLE_*: From code execution
    - AGENT_*: From agent orchestrator
    - UI_*: From user interaction
    - SYSTEM_*: From interpreter lifecycle
    """

    # Message events (LLM streaming)
    MESSAGE_START = auto()      # New message block starting
    MESSAGE_CHUNK = auto()      # Text content chunk
    MESSAGE_END = auto()        # Message complete

    # Code events
    CODE_START = auto()         # New code block
    CODE_CHUNK = auto()         # Code content
    CODE_END = auto()           # Code block complete

    # Console events (execution output)
    CONSOLE_OUTPUT = auto()     # stdout
    CONSOLE_ERROR = auto()      # stderr
    CONSOLE_ACTIVE_LINE = auto()  # Currently executing line

    # Agent events (from orchestrator)
    AGENT_SPAWN = auto()        # New agent created
    AGENT_OUTPUT = auto()       # Agent produced output
    AGENT_COMPLETE = auto()     # Agent finished successfully
    AGENT_ERROR = auto()        # Agent failed
    AGENT_CANCELLED = auto()    # Agent was cancelled

    # UI events (user interaction)
    UI_CANCEL = auto()          # User pressed Esc
    UI_MODE_CHANGE = auto()     # Mode switched (zen/standard/power/debug)
    UI_PANEL_TOGGLE = auto()    # Panel visibility toggled

    # System events
    SYSTEM_START = auto()       # Interpreter started responding
    SYSTEM_END = auto()         # Interpreter finished
    SYSTEM_ERROR = auto()       # Error occurred
    SYSTEM_TOKEN_UPDATE = auto()  # Token count updated

    # Confirmation events (for code approval)
    CONFIRMATION_REQUEST = auto()   # Asking user to approve code
    CONFIRMATION_RESPONSE = auto()  # User responded


@dataclass
class UIEvent:
    """
    A single UI event.

    Immutable event data that flows from interpreter to UI.
    Thread-safe when used with EventBus.

    Attributes:
        type: The event type (from EventType enum)
        data: Event-specific payload (varies by type)
        timestamp: When the event was created
        source: Where the event originated ("respond", "orchestrator", "computer", "ui")
    """
    type: EventType
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    source: str = "unknown"

    def __post_init__(self):
        # Ensure data is always a dict
        if self.data is None:
            self.data = {}


# Type alias for event handlers
EventHandler = Callable[[UIEvent], None]


class EventBus:
    """
    Thread-safe event bus for UI communication.

    Supports:
    - Publish/subscribe pattern for async handling
    - Polling for synchronous UI loops
    - Rate limiting for high-frequency events (optional)

    Thread Safety:
    - emit() can be called from any thread
    - poll() should only be called from the UI thread
    - subscribe/unsubscribe are thread-safe

    Example:
        bus = EventBus()

        # Register handler (in setup)
        bus.subscribe(EventType.MESSAGE_CHUNK, update_display)

        # Emit from interpreter thread
        bus.emit(UIEvent(type=EventType.MESSAGE_CHUNK, data={"content": "Hi"}))

        # Poll in UI loop
        for event in bus.drain():
            process(event)
    """

    def __init__(self, max_queue_size: int = 10000):
        self._queue: Queue[UIEvent] = Queue(maxsize=max_queue_size)
        self._handlers: Dict[EventType, List[EventHandler]] = {}
        self._global_handlers: List[EventHandler] = []
        self._lock = Lock()

        # Rate limiting (optional)
        self._last_emit_time: Dict[EventType, float] = {}
        self._rate_limits: Dict[EventType, float] = {}  # EventType -> min seconds between events

    def set_rate_limit(self, event_type: EventType, min_interval_seconds: float) -> None:
        """
        Set minimum interval between events of this type.
        Useful for high-frequency events like CONSOLE_OUTPUT.
        """
        self._rate_limits[event_type] = min_interval_seconds

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Register a handler for a specific event type"""
        with self._lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Register a handler for all event types"""
        with self._lock:
            self._global_handlers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Remove a handler for a specific event type"""
        with self._lock:
            if event_type in self._handlers:
                try:
                    self._handlers[event_type].remove(handler)
                except ValueError:
                    pass

    def unsubscribe_all(self, handler: EventHandler) -> None:
        """Remove a global handler"""
        with self._lock:
            try:
                self._global_handlers.remove(handler)
            except ValueError:
                pass

    def emit(self, event: UIEvent) -> bool:
        """
        Emit an event to the bus.

        Thread-safe. Can be called from any thread.

        Args:
            event: The event to emit

        Returns:
            True if event was queued, False if rate-limited or queue full
        """
        # Check rate limiting
        if event.type in self._rate_limits:
            now = time.time()
            min_interval = self._rate_limits[event.type]
            last_time = self._last_emit_time.get(event.type, 0)
            if now - last_time < min_interval:
                return False  # Rate limited
            self._last_emit_time[event.type] = now

        # Queue the event
        try:
            self._queue.put_nowait(event)
            return True
        except:
            # Queue is full
            return False

    def poll(self, timeout: Optional[float] = None) -> Optional[UIEvent]:
        """
        Get the next event from the queue.

        Should only be called from the UI thread.

        Args:
            timeout: Max seconds to wait. None for non-blocking.

        Returns:
            Next event, or None if queue is empty
        """
        try:
            if timeout is None:
                return self._queue.get_nowait()
            else:
                return self._queue.get(timeout=timeout)
        except Empty:
            return None

    def drain(self, max_events: int = 100) -> List[UIEvent]:
        """
        Get all pending events (up to max_events).

        Useful for batch processing in UI refresh cycle.

        Args:
            max_events: Maximum events to return

        Returns:
            List of pending events
        """
        events = []
        for _ in range(max_events):
            event = self.poll()
            if event is None:
                break
            events.append(event)
        return events

    def dispatch(self, event: UIEvent) -> None:
        """
        Dispatch an event to all registered handlers.

        Called automatically by process_pending(), or can be called
        directly for synchronous handling.

        Args:
            event: The event to dispatch
        """
        with self._lock:
            handlers = list(self._handlers.get(event.type, []))
            global_handlers = list(self._global_handlers)

        for handler in handlers:
            try:
                handler(event)
            except Exception:
                pass  # Don't let handler errors crash the UI

        for handler in global_handlers:
            try:
                handler(event)
            except Exception:
                pass

    def process_pending(self, max_events: int = 100) -> int:
        """
        Process all pending events by dispatching to handlers.

        Call this periodically in the UI loop.

        Args:
            max_events: Maximum events to process

        Returns:
            Number of events processed
        """
        events = self.drain(max_events)
        for event in events:
            self.dispatch(event)
        return len(events)

    def clear(self) -> int:
        """
        Clear all pending events.

        Returns:
            Number of events cleared
        """
        count = 0
        while self.poll() is not None:
            count += 1
        return count

    @property
    def pending_count(self) -> int:
        """Number of events waiting in the queue"""
        return self._queue.qsize()


def chunk_to_event(chunk: Dict[str, Any]) -> Optional[UIEvent]:
    """
    Convert an interpreter chunk to a UIEvent.

    Maps the legacy chunk format to the new event system.

    Args:
        chunk: Dictionary from interpreter.chat() generator

    Returns:
        UIEvent if mappable, None otherwise
    """
    chunk_type = chunk.get("type", "")
    chunk_role = chunk.get("role", "")
    chunk_format = chunk.get("format", "")

    # Message events
    if chunk_type == "message":
        if chunk.get("start"):
            return UIEvent(
                type=EventType.MESSAGE_START,
                data={"role": chunk_role},
                source="respond"
            )
        elif chunk.get("end"):
            return UIEvent(
                type=EventType.MESSAGE_END,
                data={"role": chunk_role},
                source="respond"
            )
        elif "content" in chunk:
            return UIEvent(
                type=EventType.MESSAGE_CHUNK,
                data={"content": chunk["content"], "role": chunk_role},
                source="respond"
            )

    # Code events
    if chunk_type == "code":
        if chunk.get("start"):
            return UIEvent(
                type=EventType.CODE_START,
                data={"language": chunk.get("format", "python")},
                source="respond"
            )
        elif chunk.get("end"):
            return UIEvent(
                type=EventType.CODE_END,
                data={},
                source="respond"
            )
        elif "content" in chunk:
            return UIEvent(
                type=EventType.CODE_CHUNK,
                data={"content": chunk["content"]},
                source="respond"
            )

    # Console events
    if chunk_type == "console":
        if chunk_format == "active_line":
            return UIEvent(
                type=EventType.CONSOLE_ACTIVE_LINE,
                data={"line": chunk.get("content")},
                source="computer"
            )
        elif chunk_format == "output":
            content = chunk.get("content", "")
            # Detect stderr vs stdout
            event_type = EventType.CONSOLE_ERROR if "error" in str(content).lower() else EventType.CONSOLE_OUTPUT
            return UIEvent(
                type=event_type,
                data={"content": content},
                source="computer"
            )

    # Confirmation events
    if chunk_type == "confirmation":
        return UIEvent(
            type=EventType.CONFIRMATION_REQUEST,
            data={"code": chunk.get("content", {})},
            source="respond"
        )

    # Status events
    if chunk_type == "status":
        return UIEvent(
            type=EventType.SYSTEM_TOKEN_UPDATE,
            data=chunk.get("content", {}),
            source="respond"
        )

    return None


# Singleton event bus (optional, can also create per-session)
_global_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get or create the global event bus"""
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus()
    return _global_bus


def reset_event_bus() -> None:
    """Reset the global event bus (for testing or new sessions)"""
    global _global_bus
    _global_bus = None
