#!/usr/bin/env python3
"""
ABOUTME: PhaseResult — structured return value for every pipeline phase
ABOUTME: Phases still mutate DraftContext, but now also report their own
ABOUTME: artifacts/metrics/duration so agents can consume results without
ABOUTME: reading files or parsing stdout.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class PhaseStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    DRY_RUN = "dry_run"


@dataclass
class PhaseResult:
    """Outcome of one pipeline phase run."""
    phase: str
    status: PhaseStatus = PhaseStatus.SUCCESS
    artifacts: Dict[str, Any] = field(default_factory=dict)   # files produced (name -> path)
    metrics: Dict[str, Any] = field(default_factory=dict)     # counts, scores, timings
    duration_seconds: float = 0.0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status in (PhaseStatus.SUCCESS, PhaseStatus.SKIPPED, PhaseStatus.DRY_RUN)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "status": self.status.value,
            "artifacts": {k: str(v) for k, v in self.artifacts.items()},
            "metrics": self.metrics,
            "duration_seconds": round(self.duration_seconds, 2),
            "error": self.error,
        }
