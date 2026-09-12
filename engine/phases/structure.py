#!/usr/bin/env python3
"""
ABOUTME: Structure phase — Architect and Formatter agents
ABOUTME: Creates thesis outline and applies academic formatting
"""

import time
import logging
from typing import List, Optional

from .context import DraftContext
from .results import PhaseResult, PhaseStatus
from research_brief import SectionSpec

logger = logging.getLogger(__name__)


def outline_from_sections(
    sections: List[SectionSpec],
    word_targets: dict,
    doc_type: str = "draft",
) -> str:
    """
    Render a deterministic markdown outline from user-specified sections.

    Used when custom_outline / research_brief.output_sections is provided:
    the Architect LLM step is skipped entirely — the author's structure wins.
    """
    total = word_targets.get("total", "")
    lines = [f"# Outline — {doc_type}", "", f"Target length: {total} words", ""]
    for i, s in enumerate(sections, start=1):
        words = f" (~{s.target_words} words)" if s.target_words else ""
        lines.append(f"## {i}. {s.title}{words}")
        if s.required_subsections:
            for j, sub in enumerate(s.required_subsections, start=1):
                lines.append(f"### {i}.{j} {sub}")
        if s.content_notes:
            lines.append(f"Notes: {s.content_notes}")
        lines.append("")
    return "\n".join(lines)


def _resolve_custom_sections(ctx: DraftContext) -> Optional[List[SectionSpec]]:
    """custom_outline parameter wins; brief.output_sections is the fallback."""
    if ctx.custom_outline:
        return ctx.custom_outline
    if ctx.research_brief and ctx.research_brief.output_sections:
        return ctx.research_brief.output_sections
    return None


def run_structure_phase(ctx: DraftContext) -> PhaseResult:
    """
    Execute the structure phase: Architect -> Formatter.

    Mutates ctx: architect_output, formatter_output
    Returns: PhaseResult with outline artifact paths

    If custom sections are provided (custom_outline or research_brief.output_sections),
    the outline is rendered deterministically and both LLM steps are skipped.
    """
    from utils.agent_runner import run_agent, rate_limit_delay
    from utils.venue_templates import compose_prompt_extras

    phase_start = time.time()

    if ctx.verbose:
        print("\n🏗\ufe0f  PHASE 2: STRUCTURE")

    if ctx.tracker:
        ctx.tracker.log_activity("📋 Designing thesis structure", event_type="milestone", phase="structure")
        ctx.tracker.update_phase("structure", progress_percent=25, details={"stage": "creating_outline"})
        ctx.tracker.check_cancellation()

    doc_type_labels = {
        'research_paper': 'short research paper',
        'bachelor': "bachelor's thesis",
        'master': "master's thesis",
        'phd': 'PhD dissertation',
    }
    doc_type = doc_type_labels.get(ctx.academic_level, "master's thesis")

    outline_path = ctx.folders['drafts'] / "00_outline.md"
    formatted_path = ctx.folders['drafts'] / "00_formatted_outline.md"

    venue = ctx.venue_target or (ctx.research_brief.venue_target if ctx.research_brief else None)
    prompt_extras = compose_prompt_extras(venue, ctx.academic_level)

    custom_sections = _resolve_custom_sections(ctx)

    if custom_sections:
        # -----------------------------------------------------------------
        # Deterministic path: the author's outline is authoritative
        # -----------------------------------------------------------------
        logger.info(f"Using author-specified outline ({len(custom_sections)} sections); skipping Architect/Formatter LLM steps")
        if ctx.verbose:
            print(f"   \u2705 Using author-specified outline ({len(custom_sections)} sections)")

        ctx.architect_output = outline_from_sections(custom_sections, ctx.word_targets, doc_type)
        ctx.formatter_output = ctx.architect_output
        outline_path.write_text(ctx.architect_output, encoding="utf-8")
        formatted_path.write_text(ctx.formatter_output, encoding="utf-8")

        if ctx.streamer:
            ctx.streamer.stream_outline_complete(
                outline_path=formatted_path,
                chapters_count=len(custom_sections),
            )
        if ctx.tracker:
            ctx.tracker.update_phase("structure", progress_percent=30, details={"stage": "outline_complete", "milestone": "outline_complete", "source": "custom"})

        return PhaseResult(
            phase="structure",
            status=PhaseStatus.SUCCESS,
            artifacts={"outline": str(outline_path), "formatted_outline": str(formatted_path)},
            metrics={"sections": len(custom_sections), "source": "custom", "llm_calls": 0},
            duration_seconds=time.time() - phase_start,
        )

    # -----------------------------------------------------------------------
    # LLM path (original behavior, with brief + venue injection)
    # -----------------------------------------------------------------------
    if ctx.tracker:
        ctx.tracker.log_activity("🏗\ufe0f Creating thesis outline...", event_type="info", phase="structure")

    total_words = ctx.word_targets['total']
    chapters_info = ctx.word_targets['chapters']

    outline_context = f"Create draft outline for: {ctx.topic}"
    if ctx.research_brief:
        brief_block = ctx.research_brief.to_prompt_context()
        if brief_block:
            outline_context += f"\n\n{brief_block}"
    elif ctx.blurb:
        outline_context += f"\n\nFocus/Context: {ctx.blurb}"
    outline_context += f"\n\nResearch gaps:\n{ctx.signal_output[:2000]}\n\nLength: {total_words} words ({doc_type}, {chapters_info} chapters)"
    if prompt_extras:
        outline_context += f"\n\n{prompt_extras}"

    ctx.architect_output = run_agent(
        model=ctx.model,
        name="Architect - Design Structure",
        prompt_path="prompts/02_structure/architect.md",
        user_input=outline_context,
        save_to=outline_path,
        skip_validation=ctx.skip_validation,
        verbose=ctx.verbose,
        token_tracker=ctx.token_tracker,
        token_stage="architect",
    )

    if ctx.tracker:
        ctx.tracker.log_activity("\u2705 Outline created", event_type="found", phase="structure")

    rate_limit_delay()

    # -----------------------------------------------------------------------
    # AGENT: Formatter
    # -----------------------------------------------------------------------
    ctx.formatter_output = run_agent(
        model=ctx.model,
        name="Formatter - Apply Style",
        prompt_path="prompts/02_structure/formatter.md",
        user_input=f"Apply academic formatting:\n\n{ctx.architect_output[:2500]}\n\nStyle: APA 7th edition",
        save_to=formatted_path,
        skip_validation=ctx.skip_validation,
        verbose=ctx.verbose,
        token_tracker=ctx.token_tracker,
        token_stage="formatter",
    )

    # MILESTONE: Outline Complete - Stream to user
    if ctx.streamer:
        chapters_count = ctx.formatter_output.count('## Chapter') + ctx.formatter_output.count('# Chapter')
        ctx.streamer.stream_outline_complete(
            outline_path=formatted_path,
            chapters_count=chapters_count if chapters_count > 0 else 5,
        )

    if ctx.tracker:
        ctx.tracker.update_phase("structure", progress_percent=30, details={"stage": "outline_complete", "milestone": "outline_complete"})

    rate_limit_delay()

    return PhaseResult(
        phase="structure",
        status=PhaseStatus.SUCCESS,
        artifacts={"outline": str(outline_path), "formatted_outline": str(formatted_path)},
        metrics={"llm_calls": 2},
        duration_seconds=time.time() - phase_start,
    )
