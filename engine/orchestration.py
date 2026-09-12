#!/usr/bin/env python3
"""
ABOUTME: Agent-friendly orchestration API — phases as first-class objects
ABOUTME: run_phase / run_pipeline / get_pipeline_status let external agents run
ABOUTME: individual pipeline phases, inspect progress, and resume — instead of
ABOUTME: the one-shot generate_draft() call.

Quick start for agents:
    from orchestration import PhaseName, build_context, run_phase, run_pipeline

    ctx = build_context(topic="...", research_brief=brief, output_dir=Path("out"))
    result = run_phase(PhaseName.RESEARCH, ctx)          # one phase, structured result
    status = get_pipeline_status(out / "checkpoint.json") # inspect without running
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from protocols import EventBus, PhaseEvent, PhaseEventType
from phases import DraftContext
from phases.results import PhaseResult, PhaseStatus
from research_brief import ResearchBrief
from utils.checkpoint import (
    PHASES,
    get_next_phase,
    load_checkpoint,
    save_checkpoint as _save_checkpoint_file,
)

logger = logging.getLogger(__name__)


class PhaseName(str, Enum):
    RESEARCH = "research"
    STRUCTURE = "structure"
    CITATIONS = "citations"
    COMPOSE = "compose"
    VALIDATE = "validate"
    COMPILE = "compile"


@dataclass
class PipelineStatus:
    """What has happened so far in a pipeline, read from a checkpoint file."""
    completed_phases: List[str] = field(default_factory=list)
    next_phase_to_run: Optional[str] = None
    checkpoint_path: Optional[str] = None
    last_timestamp: Optional[str] = None
    topic: Optional[str] = None
    output_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "completed_phases": self.completed_phases,
            "next_phase_to_run": self.next_phase_to_run,
            "checkpoint_path": self.checkpoint_path,
            "last_timestamp": self.last_timestamp,
            "topic": self.topic,
            "output_type": self.output_type,
        }


# ---------------------------------------------------------------------------
# Context construction (shared with generate_draft)
# ---------------------------------------------------------------------------

def build_context(
    topic: str = "",
    language: str = "en",
    academic_level: str = "master",
    output_dir: Optional[Path] = None,
    citation_style: str = "apa",
    output_type: str = "full",
    verbose: bool = True,
    blurb: Optional[str] = None,
    research_brief: Optional[ResearchBrief] = None,
    custom_outline: Optional[List[Any]] = None,
    custom_baselines: Optional[List[Any]] = None,
    custom_ablation: Optional[List[Any]] = None,
    venue_target: Optional[str] = None,
    headless: bool = False,
    dry_run: bool = False,
    event_bus: Optional[EventBus] = None,
    tracker: Any = None,
    streamer: Any = None,
    model: Any = None,               # pass a pre-built model, or None to auto-setup
    auto_setup_model: bool = True,   # False in dry-run mode
    skip_validation: bool = True,
    enforce_citation_gate: Optional[bool] = None,
    enforce_quality_gate: Optional[bool] = None,
    author_name: Optional[str] = None,
    institution: Optional[str] = None,
    department: Optional[str] = None,
    faculty: Optional[str] = None,
    advisor: Optional[str] = None,
    second_examiner: Optional[str] = None,
    location: Optional[str] = None,
    student_id: Optional[str] = None,
) -> DraftContext:
    """
    Build a fully-initialized DraftContext without running any phase.

    Shared by generate_draft() and by agents calling run_phase() directly.
    In dry_run mode no model is set up (no credentials needed, nothing runs).
    """
    from config import get_config
    from draft_generator import get_language_name, get_word_count_targets, setup_output_folders

    # Brief priority contract: brief.title overrides topic
    if research_brief is not None and research_brief.effective_topic and topic and \
            research_brief.effective_topic != topic:
        logger.info(f"ResearchBrief.title overrides topic: '{topic[:50]}' -> '{research_brief.effective_topic[:50]}'")
        topic = research_brief.effective_topic
    if research_brief is not None and not topic:
        topic = research_brief.effective_topic or ""
    if not topic:
        raise ValueError("build_context requires a topic (directly or via research_brief.title/core_question)")

    config = get_config()
    if model is None and auto_setup_model and not dry_run:
        from utils.agent_runner import setup_model
        model = setup_model()

    if output_dir is None:
        output_dir = config.paths.output_dir / "generated_draft"
    folders = setup_output_folders(Path(output_dir))

    word_targets = get_word_count_targets(academic_level)
    language_name = get_language_name(language)

    # custom_* params win over brief fields for the same knobs
    if custom_outline is None and research_brief is not None:
        custom_outline = research_brief.output_sections or None
    if custom_baselines is None and research_brief is not None:
        custom_baselines = research_brief.baselines or None
    if custom_ablation is None and research_brief is not None:
        custom_ablation = research_brief.ablation_dims or None
    if venue_target is None and research_brief is not None:
        venue_target = research_brief.venue_target

    ctx = DraftContext(
        topic=topic,
        language=language,
        academic_level=academic_level,
        output_type=output_type,
        citation_style=citation_style,
        skip_validation=skip_validation,
        enforce_citation_gate=enforce_citation_gate,
        enforce_quality_gate=enforce_quality_gate,
        verbose=False if headless else verbose,
        blurb=blurb,
        research_brief=research_brief,
        custom_outline=custom_outline,
        custom_baselines=custom_baselines,
        custom_ablation=custom_ablation,
        venue_target=venue_target,
        headless=headless,
        dry_run=dry_run,
        author_name=author_name,
        institution=institution,
        department=department,
        faculty=faculty,
        advisor=advisor,
        second_examiner=second_examiner,
        location=location,
        student_id=student_id,
        config=config,
        model=model,
        folders=folders,
        word_targets=word_targets,
        language_name=language_name,
        language_instruction=(
            f"\n\n**LANGUAGE REQUIREMENT:** Write the ENTIRE output in {language_name}. "
            f"All text, headings, and content must be in {language_name}."
        ),
        tracker=tracker,
        streamer=streamer,
        event_bus=event_bus,
    )

    # Optional token tracker
    try:
        from utils.token_tracker import TokenTracker
        ctx.token_tracker = TokenTracker(model_name=config.model.model_name)
    except Exception as e:
        logger.debug(f"TokenTracker not available: {e}")
        ctx.token_tracker = None

    return ctx


# ---------------------------------------------------------------------------
# Phase registry + execution
# ---------------------------------------------------------------------------

def _phase_registry() -> Dict[PhaseName, Callable[[DraftContext], Any]]:
    from phases import (
        run_research_phase,
        run_structure_phase,
        run_citation_management,
        run_compose_phase,
        run_validate_phase,
        run_compile_and_export,
    )
    return {
        PhaseName.RESEARCH: run_research_phase,
        PhaseName.STRUCTURE: run_structure_phase,
        PhaseName.CITATIONS: run_citation_management,
        PhaseName.COMPOSE: run_compose_phase,
        PhaseName.VALIDATE: run_validate_phase,
        PhaseName.COMPILE: run_compile_and_export,   # returns (pdf_path, docx_path)
    }


def _planned_llm_calls(phase: PhaseName, ctx: DraftContext) -> int:
    """Estimated LLM calls for a phase (used in dry-run plans)."""
    if phase == PhaseName.RESEARCH:
        return 3  # scout pipeline + scribe + signal
    if phase == PhaseName.STRUCTURE:
        if ctx.custom_outline or (ctx.research_brief and ctx.research_brief.output_sections):
            return 0  # author outline: deterministic, no LLM
        return 2  # architect + formatter
    if phase == PhaseName.CITATIONS:
        return 0
    if phase == PhaseName.COMPOSE:
        extra = len([
            s for s in (ctx.custom_outline or (ctx.research_brief.output_sections if ctx.research_brief else []) or [])
            if s.role not in ("introduction", "literature_review", "methodology", "results",
                              "discussion", "conclusion", "appendix")
        ])
        return 7 + extra
    if phase == PhaseName.VALIDATE:
        return 3  # thread + narrator + factcheck-extract
    if phase == PhaseName.COMPILE:
        return 1  # abstract
    return 0


def _planned_artifacts(phase: PhaseName, ctx: DraftContext) -> Dict[str, str]:
    """Expected output files for a phase (used in dry-run plans)."""
    root = ctx.folders.get("root", Path("."))
    plans = {
        PhaseName.RESEARCH: {
            "scout_raw": str(root / "research" / "scout_raw.md"),
            "combined_research": str(root / "research" / "combined_research.md"),
            "research_gaps": str(root / "research" / "research_gaps.md"),
        },
        PhaseName.STRUCTURE: {
            "outline": str(root / "drafts" / "00_outline.md"),
            "formatted_outline": str(root / "drafts" / "00_formatted_outline.md"),
        },
        PhaseName.CITATIONS: {
            "bibliography": str(root / "research" / "bibliography.json"),
        },
        PhaseName.COMPOSE: {
            "introduction": str(root / "drafts" / "01_introduction.md"),
            "main_body": str(root / "drafts" / "02_main_body.md"),
            "conclusion": str(root / "drafts" / "03_conclusion.md"),
        },
        PhaseName.VALIDATE: {
            "narrative_report": str(root / "drafts" / "qa_narrative_consistency.md"),
            "factcheck_report": str(root / "drafts" / "qa_factcheck.md"),
        },
        PhaseName.COMPILE: {
            "exports_dir": str(root / "exports"),
        },
    }
    return plans.get(phase, {})


def _dry_run_result(phase: PhaseName, ctx: DraftContext, start: float) -> PhaseResult:
    data: Dict[str, Any] = {"note": "dry run — nothing executed, no LLM calls, no files written"}
    if phase == PhaseName.RESEARCH:
        from phases.research import resolve_research_queries
        queries = resolve_research_queries(ctx)
        data["queries"] = queries
        data["query_count"] = len(queries)
    if phase == PhaseName.STRUCTURE:
        sections = ctx.custom_outline or (ctx.research_brief.output_sections if ctx.research_brief else None)
        data["outline_source"] = "custom" if sections else "llm"
        if sections:
            data["sections"] = [s.title for s in sections]
    if phase == PhaseName.COMPOSE:
        data["custom_sections"] = [
            s.title for s in (ctx.custom_outline or [])
            if s.role not in ("introduction", "literature_review", "methodology", "results",
                              "discussion", "conclusion", "appendix")
        ]
    return PhaseResult(
        phase=phase.value,
        status=PhaseStatus.DRY_RUN,
        artifacts=_planned_artifacts(phase, ctx),
        metrics={"planned_llm_calls": _planned_llm_calls(phase, ctx), "plan": data},
        duration_seconds=time.time() - start,
    )


def _run_with_retry(phase_func, ctx: DraftContext, phase_name: str, max_retries: int = 2) -> Any:
    """Transient-error retry, mirroring draft_generator.run_phase_with_retry."""
    from utils.agent_runner import _is_transient_error

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                logger.warning(f"[RETRY] {phase_name} attempt {attempt + 1}/{max_retries + 1}")
            return phase_func(ctx)
        except Exception as e:
            last_error = e
            if attempt < max_retries and _is_transient_error(e):
                time.sleep((2 ** attempt) * 5)
                continue
            raise
    if last_error:
        raise last_error


def _wrap_result(phase: PhaseName, raw: Any, start: float, ctx: DraftContext) -> PhaseResult:
    """Normalize a phase return value into a PhaseResult."""
    if isinstance(raw, PhaseResult):
        return raw
    if phase == PhaseName.COMPILE and isinstance(raw, tuple) and len(raw) == 2:
        pdf, docx = raw
        return PhaseResult(
            phase=phase.value,
            status=PhaseStatus.SUCCESS,
            artifacts={"pdf": str(pdf), "docx": str(docx)},
            metrics={},
            duration_seconds=time.time() - start,
        )
    return PhaseResult(
        phase=phase.value,
        status=PhaseStatus.SUCCESS,
        metrics={},
        duration_seconds=time.time() - start,
    )


def run_phase(
    phase: Union[PhaseName, str],
    ctx: DraftContext,
    *,
    resume: bool = True,
    save_checkpoint: bool = True,
    on_event: Optional[Callable[[PhaseEvent], None]] = None,
    retry: bool = True,
    max_retries: int = 2,
) -> PhaseResult:
    """
    Run a single pipeline phase with full agent-friendly controls.

    Args:
        phase: which phase to run
        ctx: DraftContext (see build_context)
        resume: if True and the phase was already completed (ctx.phase_results /
            checkpoint in output dir), return its recorded result without rerunning
        save_checkpoint: write checkpoint.json after success
        on_event: structured event callback (also fanned to ctx.event_bus)
        retry: retry transient failures
        max_retries: retry budget

    Returns:
        PhaseResult — never raises for phase failures; status=FAILED + error set.
        (Invalid arguments still raise, so callers can distinguish misuse.)
    """
    if isinstance(phase, str):
        phase = PhaseName(phase)

    start = time.time()

    def emit(type_: PhaseEventType, error: Optional[str] = None, data: Optional[Dict] = None) -> None:
        if on_event is not None:
            try:
                on_event(PhaseEvent(type=type_, phase=phase.value, data=data or {}, error=error))
            except Exception as e:
                logger.warning(f"on_event callback failed: {e}")
        if ctx.event_bus is not None:
            ctx.event_bus.emit(type_, phase=phase.value, data=data or {}, error=error)

    output_root = ctx.folders.get("root") if ctx.folders else None
    checkpoint_path = output_root / "checkpoint.json" if output_root else None

    if resume and checkpoint_path is not None and checkpoint_path.exists():
        try:
            _, completed = load_checkpoint(checkpoint_path)
            if completed == phase.value and phase.value not in ctx.phase_results:
                logger.info(f"Phase '{phase.value}' already completed (checkpoint) — skipping")
                result = PhaseResult(
                    phase=phase.value,
                    status=PhaseStatus.SKIPPED,
                    metrics={"reason": "already_completed_in_checkpoint"},
                    duration_seconds=0.0,
                )
                ctx.phase_results[phase.value] = result
                return result
        except Exception as e:
            logger.warning(f"Checkpoint check failed for resume: {e}")

    emit(PhaseEventType.PHASE_STARTED, data={"topic": ctx.topic, "dry_run": ctx.dry_run})

    if ctx.dry_run:
        result = _dry_run_result(phase, ctx, start)
        ctx.phase_results[phase.value] = result
        emit(PhaseEventType.PHASE_COMPLETED, data={"status": result.status.value})
        return result

    phase_func = _phase_registry()[phase]

    try:
        if retry:
            raw = _run_with_retry(phase_func, ctx, phase.value, max_retries=max_retries)
        else:
            raw = phase_func(ctx)

        # Phase functions return their own PhaseResult; compile returns a tuple.
        # Only fall back to ctx.phase_results when the phase returned nothing.
        if raw is None:
            raw = ctx.phase_results.get(phase.value)
        result = _wrap_result(phase, raw, start, ctx)

        if save_checkpoint and checkpoint_path is not None:
            _save_checkpoint_file(ctx, phase.value, checkpoint_path.parent)

        ctx.phase_results[phase.value] = result
        emit(
            PhaseEventType.PHASE_COMPLETED,
            data={"status": result.status.value, "metrics": result.metrics},
        )
        return result

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        logger.error(f"Phase '{phase.value}' failed: {error_msg}")
        if ctx.tracker is not None:
            try:
                ctx.tracker.mark_failed(error_msg[:200])
            except Exception:
                pass
        result = PhaseResult(
            phase=phase.value,
            status=PhaseStatus.FAILED,
            error=error_msg,
            duration_seconds=time.time() - start,
        )
        ctx.phase_results[phase.value] = result
        emit(PhaseEventType.PHASE_FAILED, error=error_msg)
        return result


def run_pipeline(
    phases: List[Union[PhaseName, str]],
    ctx: DraftContext,
    **kwargs,
) -> List[PhaseResult]:
    """
    Run selected phases in order. Stops at the first FAILED phase
    (subsequent phases are skipped and reported as SKIPPED).
    """
    results = []
    failed = False
    for p in phases:
        if failed:
            if isinstance(p, str):
                p = PhaseName(p)
            skipped = PhaseResult(
                phase=p.value, status=PhaseStatus.SKIPPED,
                metrics={"reason": "pipeline_aborted_after_failure"},
            )
            ctx.phase_results[p.value] = skipped
            results.append(skipped)
            continue
        result = run_phase(p, ctx, **kwargs)
        results.append(result)
        if result.status == PhaseStatus.FAILED:
            failed = True
    if ctx.event_bus is not None:
        ctx.event_bus.emit(PhaseEventType.PIPELINE_COMPLETED, data={
            "results": [r.to_dict() for r in results]
        })
    return results


def get_pipeline_status(checkpoint_path: Union[str, Path]) -> PipelineStatus:
    """
    Inspect a pipeline's progress from its checkpoint file without running anything.
    """
    checkpoint_path = Path(checkpoint_path)
    if not checkpoint_path.exists():
        return PipelineStatus(checkpoint_path=str(checkpoint_path))

    data, completed = load_checkpoint(checkpoint_path)
    completed_phases = []
    if completed in PHASES:
        completed_phases = PHASES[: PHASES.index(completed) + 1]
    next_phase = get_next_phase(completed) if completed else PHASES[0]

    return PipelineStatus(
        completed_phases=completed_phases,
        next_phase_to_run=next_phase,
        checkpoint_path=str(checkpoint_path),
        last_timestamp=data.get("timestamp"),
        topic=data.get("topic"),
        output_type=data.get("output_type"),
    )
