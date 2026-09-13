#!/usr/bin/env python3
"""
ABOUTME: Unified tool result envelope and path-safety helpers.
ABOUTME: Tools never raise for expected failures — they return ok()/fail() envelopes.
"""

from pathlib import Path
from typing import Dict


class ToolInputError(Exception):
    """Expected tool failure (bad args, guardrail rejection, missing artifact).

    Maps to an ok=False envelope. Unexpected exceptions are caught by the CLI
    wrapper and also become envelopes — the agent loop never sees a crash.
    """


def ok(data: Dict) -> Dict:
    """Successful tool result."""
    return {"ok": True, "data": data}


def fail(error: str, is_retryable: bool = False, **details) -> Dict:
    """Failed tool result. is_retryable=True hints transient causes (network, rate limit)."""
    result = {"ok": False, "error": error, "is_retryable": is_retryable}
    if details:
        result["details"] = details
    return result


def resolve_under_root(root: Path, rel: str) -> Path:
    """Resolve rel under root, rejecting absolute paths and directory escapes."""
    if not rel or not isinstance(rel, str):
        raise ToolInputError("path is required")
    root_resolved = Path(root).resolve()
    candidate = Path(rel)
    if candidate.is_absolute():
        raise ToolInputError(f"absolute paths not allowed: {rel}")
    full = (root_resolved / candidate).resolve()
    try:
        full.relative_to(root_resolved)
    except ValueError:
        raise ToolInputError(f"path escapes output root: {rel}") from None
    return full
