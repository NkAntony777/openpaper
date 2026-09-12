#!/usr/bin/env python3
"""
ABOUTME: Phase module exports for draft generation pipeline
ABOUTME: Each phase function takes a DraftContext, mutates it in-place,
ABOUTME: and returns a PhaseResult with its own artifacts/metrics.
"""

from .context import DraftContext
from .research import run_research_phase, resolve_research_queries, derive_queries_from_blurb
from .structure import run_structure_phase, outline_from_sections
from .citations import run_citation_management
from .compose import run_compose_phase
from .validate import run_validate_phase, match_forbidden_claims
from .compile import run_compile_and_export, run_expose_export
from .results import PhaseResult, PhaseStatus

__all__ = [
    "DraftContext",
    "run_research_phase",
    "resolve_research_queries",
    "derive_queries_from_blurb",
    "run_structure_phase",
    "outline_from_sections",
    "run_citation_management",
    "run_compose_phase",
    "run_validate_phase",
    "match_forbidden_claims",
    "run_compile_and_export",
    "run_expose_export",
    "PhaseResult",
    "PhaseStatus",
]
