#!/usr/bin/env python3
"""
ABOUTME: Agent-friendly event protocol for the draft pipeline
ABOUTME: PhaseEvent + trackers (JSONL / callback / fan-out) so agents can observe
ABOUTME: progress structurally instead of parsing stdout.

Usage:
    bus = EventBus(trackers=[JSONLineTracker(Path("events.jsonl"))])
    bus.emit(PhaseEventType.PHASE_STARTED, phase=PhaseName.RESEARCH, data={"topic": t})

The EventBus is installed on DraftContext.event_bus by generate_draft() /
orchestration.run_phase(); phases and orchestration emit through it when present.
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class PhaseEventType(str, Enum):
    PHASE_STARTED = "phase_started"
    PHASE_PROGRESS = "phase_progress"        # 0-100
    PHASE_COMPLETED = "phase_completed"
    PHASE_FAILED = "phase_failed"
    LLM_API_CALL = "llm_api_call"
    CITATION_FOUND = "citation_found"
    VALIDATION_ISSUE = "validation_issue"
    FORBIDDEN_CLAIM_DETECTED = "forbidden_claim_detected"
    PIPELINE_COMPLETED = "pipeline_completed"
    ERROR = "error"


@dataclass
class PhaseEvent:
    """One structured pipeline event, serializable as a single JSON line."""
    type: PhaseEventType
    phase: str                                    # phase name (str, not enum, for portability)
    timestamp: float = field(default_factory=time.time)
    progress_percent: Optional[int] = None
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_json(self) -> str:
        d = asdict(self)
        d["type"] = self.type.value
        return json.dumps(d, ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, line: str) -> "PhaseEvent":
        d = json.loads(line)
        d["type"] = PhaseEventType(d["type"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@runtime_checkable
class EventTracker(Protocol):
    """Anything that can receive pipeline events (duck-typed)."""

    def on_event(self, event: PhaseEvent) -> None: ...


class JSONLineTracker:
    """Append each event as one JSON line — ideal for agent consumption (tail -f / jq)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def on_event(self, event: PhaseEvent) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(event.to_json() + "\n")


class CallbackTracker:
    """Forward events to a user-provided callback."""

    def __init__(self, callback: Callable[[PhaseEvent], None]):
        self.callback = callback

    def on_event(self, event: PhaseEvent) -> None:
        self.callback(event)


class MultiTracker:
    """Fan out events to multiple trackers (e.g. JSONL + callback)."""

    def __init__(self, trackers: List[EventTracker]):
        self.trackers = list(trackers)

    def on_event(self, event: PhaseEvent) -> None:
        for t in self.trackers:
            try:
                t.on_event(event)
            except Exception as e:  # a broken observer must never kill the pipeline
                logger.warning(f"Tracker {type(t).__name__} failed: {e}")


class EventBus:
    """
    Central event emitter installed on DraftContext.event_bus.

    Emits are best-effort: failures in observers are logged, never raised.
    """

    def __init__(self, trackers: Optional[List[EventTracker]] = None):
        self._tracker = MultiTracker(trackers or [])

    def add_tracker(self, tracker: EventTracker) -> None:
        self._tracker.trackers.append(tracker)

    def emit(
        self,
        type_: PhaseEventType,
        phase: str = "",
        progress_percent: Optional[int] = None,
        data: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> PhaseEvent:
        event = PhaseEvent(
            type=type_,
            phase=phase,
            progress_percent=progress_percent,
            data=data or {},
            error=error,
        )
        self._tracker.on_event(event)
        return event


def read_events(path: Path) -> List[PhaseEvent]:
    """Read back a JSONL event log produced by JSONLineTracker."""
    events = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(PhaseEvent.from_json(line))
    return events
