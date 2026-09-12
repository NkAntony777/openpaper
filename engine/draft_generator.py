#!/usr/bin/env python3
"""
ABOUTME: Orchestrator for the draft generation pipeline
ABOUTME: Coordinates phase modules under engine/phases/ for production use

This module provides the public generate_draft() function that orchestrates
the full pipeline: Research -> Structure -> Citations -> Compose -> Validate -> Compile.

Output Structure:
    draft_output/
    ├── research/           # All research materials
    │   ├── papers/         # Individual paper summaries
    │   ├── combined_research.md
    │   ├── research_gaps.md
    │   └── bibliography.json
    ├── drafts/             # Section drafts
    ├── tools/              # Refinement prompts for Cursor
    └── exports/            # Final outputs (PDF, DOCX, MD)
"""

import sys
import warnings

# Suppress deprecation warnings from dependencies before any imports
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

import time
import shutil
import json
from pathlib import Path
import re
import logging
import traceback
import psutil
import os
from typing import Tuple, Optional, List, Dict
from datetime import datetime

# Suppress WeasyPrint stderr warnings
os.environ['WEASYPRINT_QUIET'] = '1'

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import get_config
from utils.structured_logger import StructuredLogger
from utils.agent_runner import setup_model

# Phase imports
from phases import (
    DraftContext,
    run_research_phase,
    run_structure_phase,
    run_citation_management,
    run_compose_phase,
    run_validate_phase,
    run_compile_and_export,
    run_expose_export,
)

# Checkpoint system
from utils.checkpoint import save_checkpoint, load_checkpoint, restore_context, get_next_phase

# Quality gate
from utils.quality_gate import run_quality_gate

# Configure comprehensive logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# =============================================================================
# GENERAL UTILITIES (stay in orchestrator)
# =============================================================================

def log_memory_usage(context=""):
    """Log current memory usage"""
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    mem_mb = mem_info.rss / 1024 / 1024
    logger.info(f"[MEMORY] {context}: {mem_mb:.1f} MB RSS")
    return mem_mb


def log_timing(func):
    """Decorator to log function execution time"""
    def wrapper(*args, **kwargs):
        start_time = time.time()
        func_name = func.__name__
        logger.info(f"[START] {func_name}")
        log_memory_usage(f"Before {func_name}")
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            logger.info(f"[COMPLETE] {func_name} in {elapsed:.1f}s")
            log_memory_usage(f"After {func_name}")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"[FAILED] {func_name} after {elapsed:.1f}s: {e}")
            logger.error(f"[TRACEBACK] {traceback.format_exc()}")
            raise
    return wrapper


def slugify(text: str, max_length: int = 30) -> str:
    """Convert text to a safe filename slug."""
    slug = re.sub(r'[^\w\s-]', '', text.lower())
    slug = re.sub(r'[\s_]+', '_', slug).strip('_')
    return slug[:max_length]


def run_phase_with_retry(
    phase_func,
    ctx: 'DraftContext',
    phase_name: str,
    max_retries: int = 2,
    timeout_multiplier: float = 1.5,
):
    """
    Run a pipeline phase with retry and extended timeout on failure (V3 feature).

    If a phase fails due to a transient error, retries with 50% extended timeout.
    This prevents entire pipeline failures from temporary API issues.

    Returns the phase function's return value (PhaseResult for standardized phases).

    Args:
        phase_func: The phase function to call (e.g., run_research_phase)
        ctx: DraftContext to pass to the phase
        phase_name: Name of the phase for logging
        max_retries: Maximum retry attempts (default: 2)
        timeout_multiplier: Timeout extension on retry (default: 1.5x)
    """
    from utils.agent_runner import _is_transient_error

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                logger.warning(f"[RETRY] {phase_name} attempt {attempt + 1}/{max_retries + 1}")
                if ctx.verbose:
                    print(f"   Retrying {phase_name} (attempt {attempt + 1})...")

            return phase_func(ctx)

        except Exception as e:
            last_error = e

            # Check if error is transient and worth retrying
            if attempt < max_retries and _is_transient_error(e):
                logger.warning(f"[RETRY] {phase_name} failed with transient error: {e}")
                # Exponential backoff
                backoff = (2 ** attempt) * 5  # 5s, 10s
                logger.info(f"[RETRY] Waiting {backoff}s before retry...")
                time.sleep(backoff)
                continue
            else:
                # Non-transient error or max retries reached
                raise

    # Should not reach here, but raise last error just in case
    if last_error:
        raise last_error


# =============================================================================
# LOCALIZATION: Chapter and section names in different languages
# =============================================================================
CHAPTER_NAMES = {
    'en': {
        'introduction': 'Introduction',
        'literature_review': 'Literature Review',
        'methodology': 'Methodology',
        'results': 'Results and Analysis',
        'discussion': 'Discussion',
        'conclusion': 'Conclusion',
        'references': 'References',
        'appendix': 'Appendix',
    },
    'de': {
        'introduction': 'Einleitung',
        'literature_review': 'Literaturübersicht',
        'methodology': 'Methodik',
        'results': 'Ergebnisse und Analyse',
        'discussion': 'Diskussion',
        'conclusion': 'Fazit',
        'references': 'Literaturverzeichnis',
        'appendix': 'Anhang',
    },
    'es': {
        'introduction': 'Introducción',
        'literature_review': 'Revisión de la Literatura',
        'methodology': 'Metodología',
        'results': 'Resultados y Análisis',
        'discussion': 'Discusión',
        'conclusion': 'Conclusión',
        'references': 'Referencias',
        'appendix': 'Apéndice',
    },
    'fr': {
        'introduction': 'Introduction',
        'literature_review': 'Revue de la Littérature',
        'methodology': 'Méthodologie',
        'results': 'Résultats et Analyse',
        'discussion': 'Discussion',
        'conclusion': 'Conclusion',
        'references': 'Références',
        'appendix': 'Annexe',
    },
    'it': {
        'introduction': 'Introduzione',
        'literature_review': 'Revisione della Letteratura',
        'methodology': 'Metodologia',
        'results': 'Risultati e Analisi',
        'discussion': 'Discussione',
        'conclusion': 'Conclusione',
        'references': 'Riferimenti',
        'appendix': 'Appendice',
    },
    'pt': {
        'introduction': 'Introdução',
        'literature_review': 'Revisão da Literatura',
        'methodology': 'Metodologia',
        'results': 'Resultados e Análise',
        'discussion': 'Discussão',
        'conclusion': 'Conclusão',
        'references': 'Referências',
        'appendix': 'Apêndice',
    },
}


def get_chapter_name(chapter_key: str, language: str = 'en') -> str:
    """
    Get localized chapter name.

    Args:
        chapter_key: Key like 'introduction', 'conclusion', etc.
        language: Language code ('en', 'de', 'es', 'fr', 'it', 'pt')

    Returns:
        Localized chapter name, or English fallback if not found
    """
    lang = language.split('-')[0].lower() if language else 'en'
    lang_dict = CHAPTER_NAMES.get(lang, CHAPTER_NAMES['en'])
    return lang_dict.get(chapter_key, CHAPTER_NAMES['en'].get(chapter_key, chapter_key.replace('_', ' ').title()))


def setup_output_folders(output_dir: Path) -> Dict[str, Path]:
    """
    Create the organized folder structure for draft output.

    Returns dict with paths to all subdirectories.
    """
    folders = {
        'root': output_dir,
        'research': output_dir / 'research',
        'papers': output_dir / 'research' / 'papers',
        'drafts': output_dir / 'drafts',
        'tools': output_dir / 'tools',
        'exports': output_dir / 'exports',
    }

    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)

    return folders


def get_language_name(language_code: str) -> str:
    """
    Convert language code to full language name for prompts and formatting.

    Args:
        language_code: ISO 639-1 language code (e.g., 'en-US', 'en-GB', 'es', 'fr')

    Returns:
        Full language name (e.g., 'American English', 'British English', 'Spanish', 'French')
    """
    language_map = {
        'en': 'English', 'en-US': 'American English', 'en-GB': 'British English',
        'en-AU': 'Australian English', 'en-CA': 'Canadian English',
        'en-NZ': 'New Zealand English', 'en-IE': 'Irish English',
        'en-ZA': 'South African English',
        'de': 'German', 'de-DE': 'German (Germany)', 'de-AT': 'German (Austria)',
        'de-CH': 'German (Switzerland)',
        'es': 'Spanish', 'es-ES': 'Spanish (Spain)', 'es-MX': 'Spanish (Mexico)',
        'es-AR': 'Spanish (Argentina)',
        'fr': 'French', 'fr-FR': 'French (France)', 'fr-CA': 'French (Canada)',
        'fr-BE': 'French (Belgium)',
        'it': 'Italian',
        'pt': 'Portuguese', 'pt-BR': 'Portuguese (Brazil)', 'pt-PT': 'Portuguese (Portugal)',
        'nl': 'Dutch', 'nl-NL': 'Dutch (Netherlands)', 'nl-BE': 'Dutch (Belgium)',
        'ru': 'Russian',
        'zh': 'Chinese', 'zh-CN': 'Chinese (Simplified)', 'zh-TW': 'Chinese (Traditional)',
        'ja': 'Japanese', 'ko': 'Korean', 'ar': 'Arabic', 'hi': 'Hindi',
        'sv': 'Swedish', 'no': 'Norwegian', 'da': 'Danish', 'fi': 'Finnish',
        'pl': 'Polish', 'cs': 'Czech', 'tr': 'Turkish', 'he': 'Hebrew',
        'th': 'Thai', 'vi': 'Vietnamese', 'id': 'Indonesian', 'ms': 'Malay',
        'uk': 'Ukrainian', 'ro': 'Romanian', 'hu': 'Hungarian', 'el': 'Greek',
        'bg': 'Bulgarian', 'hr': 'Croatian', 'sk': 'Slovak', 'sl': 'Slovenian',
        'et': 'Estonian', 'lv': 'Latvian', 'lt': 'Lithuanian',
    }
    return language_map.get(language_code, language_code.upper())


def get_word_count_targets(academic_level: str) -> dict:
    """
    Get word count targets for each section based on academic level.

    Args:
        academic_level: 'bachelor', 'master', 'phd', or 'research_paper'

    Returns:
        Dictionary with word count targets for each section, plus citation/time estimates
    """
    targets = {
        'research_paper': {
            'total': '3,000-5,000',
            'introduction': '600-800',
            'literature_review': '800-1,200',
            'methodology': '600-800',
            'results': '800-1,200',
            'discussion': '600-800',
            'conclusion': '400-600',
            'appendices': '0',
            'chapters': '3-4',
            'min_citations': 10,
            'deep_research_min_sources': 20,
            'estimated_time_minutes': '5-10',
        },
        'bachelor': {
            'total': '10,000-15,000',
            'introduction': '1,500-2,000',
            'literature_review': '3,000-4,000',
            'methodology': '1,500-2,000',
            'results': '2,500-3,500',
            'discussion': '1,500-2,000',
            'conclusion': '800-1,200',
            'appendices': '500-1,000',
            'chapters': '5-7',
            'min_citations': 15,
            'deep_research_min_sources': 40,
            'estimated_time_minutes': '8-15',
        },
        'master': {
            'total': '25,000-30,000',
            'introduction': '2,500-3,000',
            'literature_review': '6,000-7,000',
            'methodology': '3,000-3,500',
            'results': '6,000-7,000',
            'discussion': '3,000-3,500',
            'conclusion': '1,500-2,000',
            'appendices': '2,000-3,000',
            'chapters': '7-10',
            'min_citations': 25,
            'deep_research_min_sources': 50,
            'estimated_time_minutes': '10-25',
        },
        'phd': {
            'total': '50,000-80,000',
            'introduction': '4,000-5,000',
            'literature_review': '12,000-15,000',
            'methodology': '6,000-8,000',
            'results': '12,000-15,000',
            'discussion': '8,000-10,000',
            'conclusion': '3,000-4,000',
            'appendices': '5,000-8,000',
            'chapters': '10-15',
            'min_citations': 50,
            'deep_research_min_sources': 100,
            'estimated_time_minutes': '20-40',
        },
    }
    return targets.get(academic_level, targets['master'])


class PipelineValidationError(ValueError):
    """Raised when inter-phase validation fails."""
    pass


def validate_research_phase(ctx: 'DraftContext') -> None:
    """Validate research phase outputs before proceeding to structure."""
    if not ctx.scout_result:
        raise PipelineValidationError("Research phase failed: scout_result is empty")

    citations = ctx.scout_result.get('citations', [])
    if not citations:
        raise PipelineValidationError("Research phase failed: no citations found")

    min_citations = ctx.word_targets.get('min_citations', 10)
    # Allow proceeding with fewer citations, but warn
    if len(citations) < min_citations // 2:
        logger.warning(f"Research found only {len(citations)} citations (target: {min_citations})")


def validate_structure_phase(ctx: 'DraftContext') -> None:
    """Validate structure phase outputs before proceeding to citation management."""
    if not ctx.architect_output:
        raise PipelineValidationError("Structure phase failed: architect_output is empty")

    # Check for basic outline structure
    if '##' not in ctx.architect_output and '#' not in ctx.architect_output:
        logger.warning("Structure output may be malformed: no markdown headers found")


def validate_citation_phase(ctx: 'DraftContext') -> None:
    """Validate citation management outputs before proceeding to compose."""
    if not ctx.citation_database:
        raise PipelineValidationError("Citation phase failed: citation_database is empty")

    if not ctx.citation_database.citations:
        raise PipelineValidationError("Citation phase failed: no citations in database")

    if not ctx.citation_summary:
        logger.warning("Citation summary is empty - writers may not cite correctly")


def validate_compose_phase(ctx: 'DraftContext') -> None:
    """Validate compose phase outputs before proceeding to compile."""
    if not ctx.intro_output:
        raise PipelineValidationError("Compose phase failed: introduction is empty")

    # Check at least some body content exists
    body_sections = [ctx.lit_review_output, ctx.methodology_output, ctx.results_output, ctx.discussion_output]
    filled_sections = sum(1 for s in body_sections if s)

    if filled_sections == 0:
        raise PipelineValidationError("Compose phase failed: no body sections generated")


def copy_tools_to_output(tools_dir: Path, topic: str, academic_level: str, verbose: bool = True):
    """Copy refinement prompts and create .cursorrules for the output folder."""
    project_root = Path(__file__).parent.parent

    voice_src = project_root / 'prompts' / '05_refine' / 'voice.md'
    if voice_src.exists():
        shutil.copy(voice_src, tools_dir / 'humanizer_prompt.md')

    entropy_src = project_root / 'prompts' / '05_refine' / 'entropy.md'
    if entropy_src.exists():
        shutil.copy(entropy_src, tools_dir / 'entropy_prompt.md')

    style_src = project_root / 'templates' / 'style_guide.md'
    if style_src.exists():
        shutil.copy(style_src, tools_dir / 'style_guide.md')

    cursorrules_template = project_root / 'templates' / 'cursorrules.md'
    if cursorrules_template.exists():
        content = cursorrules_template.read_text(encoding='utf-8')
        content = content.replace('{topic}', topic)
        content = content.replace('{academic_level}', academic_level)
        (tools_dir / '.cursorrules').write_text(content, encoding='utf-8')

    if verbose:
        print("   \u2705 Copied refinement tools to output")


def create_output_readme(output_dir: Path, topic: str, verbose: bool = True):
    """Create README.md and CLAUDE.md for the output folder."""
    project_root = Path(__file__).parent.parent
    readme_template = project_root / 'templates' / 'draft_readme.md'
    claude_template = project_root / 'templates' / 'claude.md'

    if readme_template.exists():
        shutil.copy(readme_template, output_dir / 'README.md')
        if verbose:
            print("   \u2705 Created README.md")

    if claude_template.exists():
        shutil.copy(claude_template, output_dir / 'CLAUDE.md')
        if verbose:
            print("   \u2705 Created CLAUDE.md")


# =============================================================================
# MAIN ORCHESTRATOR
# =============================================================================

def generate_draft(
    topic: str,
    language: str = "en",
    academic_level: str = "master",
    output_dir: Optional[Path] = None,
    skip_validation: bool = True,
    enforce_citation_gate: Optional[bool] = None,
    enforce_quality_gate: Optional[bool] = None,
    verbose: bool = True,
    tracker=None,
    streamer=None,
    blurb: Optional[str] = None,
    output_type: str = "full",
    author_name: Optional[str] = None,
    institution: Optional[str] = None,
    department: Optional[str] = None,
    faculty: Optional[str] = None,
    advisor: Optional[str] = None,
    second_examiner: Optional[str] = None,
    location: Optional[str] = None,
    student_id: Optional[str] = None,
    citation_style: str = "apa",
    resume_from: Optional[Path] = None,
    # --- structured research intent (priority: research_brief > blurb > topic) ---
    research_brief=None,
    brief_path: Optional[Path] = None,
    custom_outline: Optional[List] = None,
    custom_baselines: Optional[List] = None,
    custom_ablation: Optional[List] = None,
    venue_target: Optional[str] = None,
    # --- agent-friendly execution modes ---
    headless: bool = False,
    dry_run: bool = False,
    events_path: Optional[Path] = None,
):
    """
    Generate a complete academic draft using specialized AI agents.

    This is a simplified, production-ready version of the test workflow,
    optimized for automated processing on Modal.com or similar platforms.

    Args:
        topic: Draft topic (e.g., "Machine Learning for Climate Prediction")
        language: Draft language code (e.g., 'en-US', 'en-GB', 'de', 'es', 'fr', etc.)
        academic_level: 'bachelor', 'master', or 'phd'
        output_dir: Custom output directory (default: config.paths.output_dir / "generated_draft")
        skip_validation: Skip strict quality gates (recommended for automated runs)
        verbose: Print progress messages
        output_type: 'full' for complete draft, 'expose' for research overview only
        author_name: Student's full name (for cover page)
        institution: University/institution name
        department: Department name
        faculty: Faculty name
        advisor: First examiner/advisor name
        second_examiner: Second examiner name
        location: City/location for date line
        student_id: Student matriculation number
        citation_style: Citation format - 'apa' or 'ieee' (default: 'apa')
        resume_from: Path to checkpoint.json to resume from (skips completed phases)
        research_brief: research_brief.ResearchBrief — structured research intent
            (RQs, hypotheses, tasks, innovations, baselines, ablations, metrics,
            split strategy, GT protocol, forbidden claims, venue). When given it
            takes precedence over topic+blurb everywhere in the pipeline.
        brief_path: Path to a brief file (.yaml/.yml/.json); loaded into research_brief
        custom_outline: List[SectionSpec] — author-specified outline; overrides LLM outline
        custom_baselines: List[BaselineSpec] — baselines the paper must cover
        custom_ablation: List[AblationSpec] — ablation dimensions to cover
        venue_target: Target venue (e.g. "ICWSM", "WWW") — injects venue template
        headless: Suppress all human-oriented prints (verbose is forced False)
        dry_run: Plan only — no LLM calls, no files written; returns List[PhaseResult]
        events_path: Write a JSONL event stream to this path (agent observation)

    Returns:
        Tuple[Path, Path]: (pdf_path, docx_path) - Paths to generated draft files
        List[PhaseResult]: when dry_run=True

    Raises:
        ValueError: If insufficient citations found or generation fails
        Exception: If any critical step fails
    """
    # ====================================================================
    # STARTUP AND INITIALIZATION
    # ====================================================================
    draft_start_time = time.time()

    # Load brief from file if given
    if brief_path is not None and research_brief is None:
        from research_brief import ResearchBrief
        research_brief = ResearchBrief.from_file(brief_path)
        logger.info(f"Loaded research brief from {brief_path}")

    # Brief priority: brief.title replaces the topic string
    if research_brief is not None and research_brief.effective_topic:
        if topic and topic != research_brief.effective_topic:
            logger.info(
                f"ResearchBrief overrides topic: '{topic[:60]}' -> '{research_brief.effective_topic[:60]}'"
            )
        topic = research_brief.effective_topic

    if headless:
        verbose = False

    # Structured event stream (agent-friendly observation)
    event_bus = None
    if events_path is not None:
        from protocols import EventBus, JSONLineTracker, PhaseEventType
        event_bus = EventBus(trackers=[JSONLineTracker(Path(events_path))])
        event_bus.emit(PhaseEventType.PHASE_STARTED, phase="pipeline", data={
            "topic": topic,
            "dry_run": dry_run,
            "has_brief": research_brief is not None,
        })

    logger.info("=" * 80)
    logger.info("DRAFT GENERATION STARTED")
    logger.info("=" * 80)
    logger.info(f"Topic: {topic}")
    logger.info(f"Language: {language}")
    logger.info(f"Academic Level: {academic_level}")
    logger.info(f"Output Type: {output_type}")
    logger.info(f"Validation: {'Skipped' if skip_validation else 'Enabled'}")
    if research_brief is not None:
        logger.info(f"ResearchBrief: ACTIVE ({len(research_brief.research_questions)} RQs, "
                    f"{len(research_brief.hypotheses)} hypotheses, "
                    f"{len(research_brief.baselines)} baselines, "
                    f"{len(research_brief.ablation_dims)} ablations)")
    if custom_outline:
        logger.info(f"Custom outline: {len(custom_outline)} author-specified sections")
    if venue_target:
        logger.info(f"Venue target: {venue_target}")
    if dry_run:
        logger.info("Mode: DRY RUN (planning only, no LLM calls, no file writes)")
    if headless:
        logger.info("Mode: HEADLESS")
    if enforce_citation_gate is not None:
        logger.info(f"Citation Gate: {'Enforced' if enforce_citation_gate else 'Bypassed'}")
    if enforce_quality_gate is not None:
        logger.info(f"Draft Quality Gate: {'Enforced' if enforce_quality_gate else 'Bypassed'}")
    logger.info(f"Tracker: {'Enabled' if tracker else 'Disabled'}")
    logger.info(f"Streamer: {'Enabled' if streamer else 'Disabled'}")
    if author_name:
        logger.info(f"Author: {author_name}")
    if institution:
        logger.info(f"Institution: {institution}")
    logger.info(f"Process PID: {os.getpid()}")
    logger.info(f"Python: {sys.version}")
    log_memory_usage("Initial")
    logger.info("=" * 80)

    # Immediate progress update
    local_progress_enabled = os.getenv("OPENDRAFT_LOCAL_PROGRESS", "").strip().lower() in {"1", "true", "yes", "on"}
    if tracker is None and local_progress_enabled:
        from utils.progress_tracker import ProgressTracker
        tracker = ProgressTracker(draft_id=os.getenv("OPENDRAFT_LOCAL_PROGRESS_ID") or "local", local_only=True)

    if tracker:
        tracker.log_activity("🚀 Generation started", event_type="milestone", phase="research")
        tracker.update_phase("research", progress_percent=1, details={"stage": "initializing"})

    try:
        config = get_config()

        # ----------------------------------------------------------------
        # DRY RUN: build the plan and return it without executing anything
        # ----------------------------------------------------------------
        if dry_run:
            from orchestration import PhaseName, build_context, run_pipeline
            ctx = build_context(
                topic=topic, language=language, academic_level=academic_level,
                output_dir=output_dir, citation_style=citation_style,
                output_type=output_type, verbose=verbose, blurb=blurb,
                research_brief=research_brief, custom_outline=custom_outline,
                custom_baselines=custom_baselines, custom_ablation=custom_ablation,
                venue_target=venue_target, headless=headless, dry_run=True,
                event_bus=event_bus, tracker=tracker, streamer=streamer,
                model=None, auto_setup_model=False,
                skip_validation=skip_validation,
                enforce_citation_gate=enforce_citation_gate,
                enforce_quality_gate=enforce_quality_gate,
            )
            phases = [PhaseName.RESEARCH, PhaseName.STRUCTURE, PhaseName.CITATIONS]
            if output_type != "expose":
                phases += [PhaseName.COMPOSE, PhaseName.VALIDATE, PhaseName.COMPILE]
            results = run_pipeline(phases, ctx)
            if event_bus is not None:
                from protocols import PhaseEventType
                event_bus.emit(PhaseEventType.PIPELINE_COMPLETED, phase="pipeline",
                               data={"dry_run": True, "results": [r.to_dict() for r in results]})
            return results

        # Check CLI quiet mode
        from utils.api_citations.orchestrator import _verbose_research
        cli_quiet_mode = not _verbose_research

        if verbose and not cli_quiet_mode:
            print("=" * 70)
            print("DRAFT GENERATION - AUTOMATED WORKFLOW")
            print("=" * 70)
            print(f"Topic: {topic}")
            print(f"Language: {language}")
            print(f"Level: {academic_level}")
            print(f"Validation: {'Skipped' if skip_validation else 'Enabled'}")
            print("=" * 70)

        # Setup model
        logger.info("[SETUP] Initializing Gemini model...")
        if tracker:
            tracker.log_activity("🤖 Loading AI model...", event_type="info", phase="research")

        model = setup_model()
        logger.info("[SETUP] Model initialized successfully")

        if tracker:
            tracker.log_activity("\u2705 AI model ready", event_type="found", phase="research")
            tracker.update_phase("research", progress_percent=3, details={"stage": "model_loaded"})

        if output_dir is None:
            output_dir = config.paths.output_dir / "generated_draft"

        logger.info(f"[SETUP] Output directory: {output_dir}")

        folders = setup_output_folders(output_dir)
        logger.info(f"[SETUP] Created folders: {', '.join(folders.keys())}")

        if verbose and not cli_quiet_mode:
            print(f"📁 Output folder: {output_dir}")

        if tracker and local_progress_enabled:
            tracker.set_local_progress_path(str(output_dir / "progress.json"))

        # Prepare word targets and language
        word_targets = get_word_count_targets(academic_level)
        language_name = get_language_name(language)
        language_instruction = f"\n\n**LANGUAGE REQUIREMENT:** Write the ENTIRE output in {language_name}. All text, headings, and content must be in {language_name}."

        # ====================================================================
        # Initialize DraftContext (structured brief included)
        # ====================================================================
        # custom_* params win over brief fields for the same knobs
        eff_outline = custom_outline or (research_brief.output_sections if research_brief else None)
        eff_baselines = custom_baselines or (research_brief.baselines if research_brief else None)
        eff_ablation = custom_ablation or (research_brief.ablation_dims if research_brief else None)
        eff_venue = venue_target or (research_brief.venue_target if research_brief else None)

        ctx = DraftContext(
            topic=topic,
            language=language,
            academic_level=academic_level,
            output_type=output_type,
            citation_style=citation_style,
            skip_validation=skip_validation,
            enforce_citation_gate=enforce_citation_gate,
            enforce_quality_gate=enforce_quality_gate,
            verbose=verbose,
            blurb=blurb,
            research_brief=research_brief,
            custom_outline=eff_outline or None,
            custom_baselines=eff_baselines or None,
            custom_ablation=eff_ablation or None,
            venue_target=eff_venue,
            headless=headless,
            dry_run=False,
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
            language_instruction=language_instruction,
            tracker=tracker,
            streamer=streamer,
            event_bus=event_bus,
        )

        # Optional token tracker
        try:
            from utils.token_tracker import TokenTracker
            model_name = config.model.model_name
            ctx.token_tracker = TokenTracker(model_name=model_name)
            logger.info(f"[SETUP] TokenTracker initialized for {model_name}")
        except Exception as e:
            logger.debug(f"[SETUP] TokenTracker not available: {e}")
            ctx.token_tracker = None

        # ====================================================================
        # Handle resume from checkpoint
        # ====================================================================
        completed_phase = None
        if resume_from and resume_from.exists():
            logger.info(f"Resuming from checkpoint: {resume_from}")
            checkpoint_data, completed_phase = load_checkpoint(resume_from)

            # Warn if topic doesn't match checkpoint
            checkpoint_topic = checkpoint_data.get("topic", "")
            if topic and checkpoint_topic and topic != checkpoint_topic:
                logger.warning(f"Topic mismatch: CLI='{topic[:50]}' vs checkpoint='{checkpoint_topic[:50]}'")
                if verbose:
                    print(f"   Warning: Using checkpoint topic, not CLI topic")

            restore_context(ctx, checkpoint_data)

            # Reload citation database if citations phase was completed
            if completed_phase in ["citations", "compose", "validate", "compile"]:
                from utils.citation_database import load_citation_database
                bibliography_path = ctx.folders['research'] / "bibliography.json"
                if bibliography_path.exists():
                    ctx.citation_database = load_citation_database(bibliography_path)
                    logger.info(f"Restored citation database: {len(ctx.citation_database.citations)} citations")

            if verbose:
                print(f"   Resumed from checkpoint (completed: {completed_phase})")

        # ====================================================================
        # Execute pipeline phases with inter-phase validation and checkpoints
        # ====================================================================

        # RESEARCH PHASE (with pipeline-level retry)
        if not completed_phase or get_next_phase(completed_phase) == "research":
            if completed_phase:
                logger.info("Starting fresh (no phases completed yet)")
            ctx.phase_results["research"] = run_phase_with_retry(run_research_phase, ctx, "research")
            validate_research_phase(ctx)
            save_checkpoint(ctx, "research", output_dir)
            completed_phase = "research"

        # STRUCTURE PHASE (with pipeline-level retry)
        if get_next_phase(completed_phase) == "structure" or completed_phase == "research":
            ctx.phase_results["structure"] = run_phase_with_retry(run_structure_phase, ctx, "structure")
            validate_structure_phase(ctx)
            save_checkpoint(ctx, "structure", output_dir)
            completed_phase = "structure"

        # CITATIONS PHASE (with pipeline-level retry)
        if get_next_phase(completed_phase) == "citations" or completed_phase == "structure":
            ctx.phase_results["citations"] = run_phase_with_retry(run_citation_management, ctx, "citations")
            validate_citation_phase(ctx)
            save_checkpoint(ctx, "citations", output_dir)
            completed_phase = "citations"

        # EXPOSE MODE: Early exit after citations
        if ctx.output_type == 'expose':
            pdf_path, docx_path = run_expose_export(ctx)
            _finalize(ctx, pdf_path, docx_path, draft_start_time)
            return pdf_path, docx_path

        # COMPOSE PHASE (with pipeline-level retry)
        if get_next_phase(completed_phase) == "compose" or completed_phase == "citations":
            ctx.phase_results["compose"] = run_phase_with_retry(run_compose_phase, ctx, "compose")
            validate_compose_phase(ctx)
            save_checkpoint(ctx, "compose", output_dir)
            completed_phase = "compose"

        # QUALITY GATE (after compose, before validate)
        quality_gate_strict = enforce_quality_gate if enforce_quality_gate is not None else not skip_validation
        quality_result = run_quality_gate(ctx, strict=quality_gate_strict)
        if verbose:
            print(f"   Quality Score: {quality_result.total_score}/100")
            if quality_result.issues:
                for issue in quality_result.issues[:3]:  # Show top 3 issues
                    print(f"   ⚠ {issue}")

        # VALIDATE PHASE (skip if quality is very high)
        if quality_result.total_score >= 85:
            logger.info(f"Quality score {quality_result.total_score} >= 85, skipping QA phase")
            if verbose:
                print("   ✓ High quality - skipping QA phase")
            completed_phase = "validate"  # Mark as complete
        elif get_next_phase(completed_phase) == "validate" or completed_phase == "compose":
            ctx.phase_results["validate"] = run_phase_with_retry(run_validate_phase, ctx, "validate")
            save_checkpoint(ctx, "validate", output_dir)
            completed_phase = "validate"

        # Copy tools and README
        if not headless:
            copy_tools_to_output(folders['tools'], topic, academic_level, verbose)
            create_output_readme(output_dir, topic, verbose)

        pdf_path, docx_path = run_compile_and_export(ctx)

        # Record compile as a structured result too (agent-friendly)
        from phases.results import PhaseResult, PhaseStatus
        ctx.phase_results["compile"] = PhaseResult(
            phase="compile",
            status=PhaseStatus.SUCCESS,
            artifacts={"pdf": str(pdf_path), "docx": str(docx_path)},
            duration_seconds=time.time() - draft_start_time,
        )

        _finalize(ctx, pdf_path, docx_path, draft_start_time)
        return pdf_path, docx_path

    except Exception as e:
        draft_total_time = time.time() - draft_start_time
        logger.error("=" * 80)
        logger.error("DRAFT GENERATION FAILED!")
        logger.error("=" * 80)
        logger.error(f"Failed after {draft_total_time:.1f}s ({draft_total_time/60:.1f} minutes)")
        logger.error(f"Error: {e}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error("=" * 80)
        logger.error("FULL TRACEBACK:")
        logger.error(traceback.format_exc())
        logger.error("=" * 80)
        log_memory_usage("At failure")

        if tracker:
            try:
                tracker.mark_failed(f"{type(e).__name__}: {str(e)[:200]}")
            except Exception as tracker_error:
                logger.error(f"Failed to update tracker: {tracker_error}")

        if event_bus is not None:
            try:
                from protocols import PhaseEventType
                event_bus.emit(PhaseEventType.PHASE_FAILED, phase="pipeline",
                               error=f"{type(e).__name__}: {str(e)[:500]}")
            except Exception:
                pass

        raise


def _finalize(ctx: DraftContext, pdf_path: Path, docx_path: Path, draft_start_time: float) -> None:
    """Print final report, save token usage, mark tracker complete."""
    # Token usage report
    if ctx.token_tracker:
        ctx.token_tracker.print_report()
        try:
            token_json_path = ctx.folders['root'] / "token_usage.json"
            token_json_path.write_text(ctx.token_tracker.to_json(), encoding='utf-8')
            logger.info(f"Token usage saved to {token_json_path}")
        except Exception as e:
            logger.warning(f"Failed to save token usage: {e}")

    if ctx.tracker:
        ctx.tracker.mark_completed()

    if getattr(ctx, "event_bus", None):
        try:
            from protocols import PhaseEventType
            ctx.event_bus.emit(
                PhaseEventType.PIPELINE_COMPLETED,
                phase="pipeline",
                progress_percent=100,
                data={
                    "pdf": str(pdf_path) if pdf_path else None,
                    "docx": str(docx_path) if docx_path else None,
                    "total_seconds": time.time() - draft_start_time,
                },
            )
        except Exception as e:
            logger.warning(f"Event emission failed: {e}")

    if ctx.verbose:
        print("=" * 70)
        print("\u2705 DRAFT GENERATION COMPLETE")
        print("=" * 70)
        print(f"\u2705 Exported PDF: {pdf_path}")
        print(f"\u2705 Exported DOCX: {docx_path}")
        print(f"📂 Output folder: {ctx.folders['root']}")
        print("\n💡 Open the folder in Cursor to refine your draft!")
        print(f"   cursor {ctx.folders['root']}")

    draft_total_time = time.time() - draft_start_time
    logger.info("=" * 80)
    logger.info("DRAFT GENERATION COMPLETE!")
    logger.info("=" * 80)
    logger.info(f"Total time: {draft_total_time:.1f}s ({draft_total_time/60:.1f} minutes)")
    logger.info(f"PDF: {pdf_path}")
    logger.info(f"DOCX: {docx_path}")
    if pdf_path.exists():
        logger.info(f"PDF size: {pdf_path.stat().st_size:,} bytes ({pdf_path.stat().st_size/1024/1024:.1f} MB)")
    if docx_path.exists():
        logger.info(f"DOCX size: {docx_path.stat().st_size:,} bytes ({docx_path.stat().st_size/1024/1024:.1f} MB)")
    log_memory_usage("Final")
    logger.info("=" * 80)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate academic draft")

    # Required arguments
    parser.add_argument("--topic", required=True, help="Draft topic")
    parser.add_argument("--language", default="en", help="Language code (e.g., en-US, en-GB, de, es, fr, etc.)")
    parser.add_argument("--academic-level", default="master", choices=["research_paper", "bachelor", "master", "phd"], help="Academic level")

    # Database integration (optional)
    parser.add_argument("--draft-id", help="Database draft ID for progress tracking")
    parser.add_argument("--supabase-url", help="Supabase URL")
    parser.add_argument("--supabase-key", help="Supabase service role key")
    parser.add_argument("--gemini-key", help="Gemini API key (overrides env var)")

    # Metadata (optional)
    parser.add_argument("--author", help="Author name")
    parser.add_argument("--institution", help="Institution name")
    parser.add_argument("--department", help="Department name")
    parser.add_argument("--faculty", help="Faculty name")
    parser.add_argument("--advisor", help="Advisor name")
    parser.add_argument("--second-examiner", help="Second examiner name")
    parser.add_argument("--location", help="Location")
    parser.add_argument("--student-id", help="Student ID")

    # Other options
    parser.add_argument("--validate", action="store_true", help="Enable strict validation")

    args = parser.parse_args()

    # Set environment variables if provided
    if args.gemini_key:
        os.environ['GEMINI_API_KEY'] = args.gemini_key
    if args.supabase_url:
        os.environ['SUPABASE_URL'] = args.supabase_url
    if args.supabase_key:
        os.environ['SUPABASE_SERVICE_KEY'] = args.supabase_key

    # Initialize database tracker if draft_id provided
    progress_tracker = None
    if args.draft_id:
        from utils.progress_tracker import ProgressTracker
        progress_tracker = ProgressTracker(draft_id=args.draft_id)
        print(f"\u2705 Database tracking enabled for draft: {args.draft_id}")

    try:
        pdf, docx = generate_draft(
            topic=args.topic,
            language=args.language,
            academic_level=args.academic_level,
            skip_validation=not args.validate,
            tracker=progress_tracker,
            author_name=args.author,
            institution=args.institution,
            department=args.department,
            faculty=args.faculty,
            advisor=args.advisor,
            second_examiner=args.second_examiner,
            location=args.location,
            student_id=args.student_id,
        )

        print(f"\n\u2705 Generated:")
        print(f"   PDF: {pdf}")
        print(f"   DOCX: {docx}")

        sys.exit(0)

    except Exception as e:
        print(f"\n\u274c Generation failed: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
