#!/usr/bin/env python3
"""
ABOUTME: DraftContext dataclass — mutable shared state for inter-phase communication
ABOUTME: Each phase reads inputs from ctx and writes outputs back to ctx
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from research_brief import AblationSpec, BaselineSpec, ResearchBrief, SectionSpec


@dataclass
class DraftContext:
    """
    Mutable inter-phase communication bus for draft generation.

    Each phase function takes a DraftContext, reads its inputs,
    and writes its outputs back onto the same object.
    """

    # ------------------------------------------------------------------
    # User inputs (set once at initialization)
    # ------------------------------------------------------------------
    topic: str = ""
    language: str = "en"
    academic_level: str = "master"
    output_type: str = "full"  # 'full' or 'expose'
    citation_style: str = "apa"  # 'apa', 'ieee', or 'nalt'
    skip_validation: bool = True
    enforce_citation_gate: Optional[bool] = None
    enforce_quality_gate: Optional[bool] = None
    verbose: bool = True
    blurb: Optional[str] = None

    # Structured research intent (priority: research_brief > blurb > topic)
    research_brief: Optional[ResearchBrief] = None
    custom_outline: Optional[List[SectionSpec]] = None
    custom_baselines: Optional[List[BaselineSpec]] = None
    custom_ablation: Optional[List[AblationSpec]] = None
    venue_target: Optional[str] = None

    # Agent-friendly execution modes
    headless: bool = False  # suppress all human-oriented prints
    dry_run: bool = False  # plan only: no LLM calls, no file writes

    # Academic metadata (optional, for cover page)
    author_name: Optional[str] = None
    institution: Optional[str] = None
    department: Optional[str] = None
    faculty: Optional[str] = None
    advisor: Optional[str] = None
    second_examiner: Optional[str] = None
    location: Optional[str] = None
    student_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Infrastructure (set during initialization)
    # ------------------------------------------------------------------
    config: Any = None  # AppConfig instance
    model: Any = None  # GenerativeModel instance
    folders: Dict[str, Path] = field(default_factory=dict)
    word_targets: Dict[str, Any] = field(default_factory=dict)
    language_name: str = ""
    language_instruction: str = ""

    # Progress reporting (optional)
    tracker: Any = None  # ProgressTracker
    streamer: Any = None  # MilestoneStreamer
    event_bus: Any = None  # protocols.EventBus (structured event stream)

    # Structured per-phase results (phase name -> PhaseResult)
    phase_results: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Research phase outputs
    # ------------------------------------------------------------------
    scout_result: Optional[Dict[str, Any]] = None
    scout_output: str = ""
    scribe_output: str = ""
    signal_output: str = ""

    # ------------------------------------------------------------------
    # Structure phase outputs
    # ------------------------------------------------------------------
    architect_output: str = ""
    formatter_output: str = ""

    # ------------------------------------------------------------------
    # Citation management outputs
    # ------------------------------------------------------------------
    citation_database: Any = None  # CitationDatabase
    citation_summary: str = ""

    # ------------------------------------------------------------------
    # Compose phase outputs
    # ------------------------------------------------------------------
    intro_output: str = ""
    lit_review_output: str = ""
    methodology_output: str = ""
    results_output: str = ""
    discussion_output: str = ""
    body_output: str = ""
    conclusion_output: str = ""
    appendix_output: str = ""

    # ------------------------------------------------------------------
    # Token tracking (optional)
    # ------------------------------------------------------------------
    token_tracker: Any = None  # TokenTracker
