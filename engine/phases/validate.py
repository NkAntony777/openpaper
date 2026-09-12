#!/usr/bin/env python3
"""
ABOUTME: Validation phase — Thread, Narrator, FactCheck QA agents
ABOUTME: Narrative consistency, voice unification, and factual verification
"""

import json
import re
import time
import logging
from typing import List, Tuple

from .context import DraftContext
from .results import PhaseResult, PhaseStatus

logger = logging.getLogger(__name__)


# English stopwords for forbidden-claim keyword matching
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "that", "this", "these", "those", "is", "are", "was", "were", "be",
    "been", "has", "have", "had", "we", "our", "it", "its", "as", "by",
    "at", "from", "not", "no", "but", "can", "could", "will", "would",
}


def _claim_keywords(claim: str, min_len: int = 4) -> List[str]:
    """Extract meaningful keywords from a forbidden-claim sentence."""
    words = re.findall(r"[a-zA-Z\u4e00-\u9fff]+", claim.lower())
    return [w for w in words if len(w) >= min_len and w not in _STOPWORDS]


def match_forbidden_claims(
    text: str,
    forbidden_claims: List[str],
    match_threshold: float = 0.6,
    min_keywords: int = 2,
) -> List[Tuple[str, float, List[str]]]:
    """
    Detect forbidden claims in draft text via keyword overlap.

    A claim is flagged when at least `match_threshold` of its meaningful
    keywords appear in the text (and at least `min_keywords` matched).
    Pure-text matching: deterministic, testable, no LLM needed.

    Returns list of (claim, match_ratio, matched_keywords).
    """
    lowered = text.lower()
    hits = []
    for claim in forbidden_claims:
        keywords = _claim_keywords(claim)
        if not keywords:
            continue
        matched = [k for k in keywords if k in lowered]
        ratio = len(matched) / len(keywords)
        if len(matched) >= min_keywords and ratio >= match_threshold:
            hits.append((claim, ratio, matched))
    return hits


def run_validate_phase(ctx: DraftContext) -> PhaseResult:
    """
    Execute the QA phase: Thread -> Narrator -> FactCheck -> Forbidden-claims audit.

    Writes QA report files to drafts/ folder. No ctx mutations.
    Returns: PhaseResult with report artifacts and issue counts.
    """
    from utils.agent_runner import run_agent, rate_limit_delay

    phase_start = time.time()

    logger.info("=" * 80)
    logger.info("PHASE 3.5: QUALITY ASSURANCE - Narrative consistency & voice unification")
    logger.info("=" * 80)

    if ctx.verbose:
        print("\n🔍 PHASE 3.5: QUALITY ASSURANCE")

    # Build QA review content from chapter outputs
    all_chapters_for_qa = _build_qa_content(ctx)

    # --- QA STEP 1: Thread ---
    _run_thread(ctx, all_chapters_for_qa)
    rate_limit_delay()

    # --- QA STEP 2: Narrator ---
    _run_narrator(ctx, all_chapters_for_qa)
    rate_limit_delay()

    # --- QA STEP 3: FactCheck ---
    factcheck_claims = _run_factcheck(ctx, all_chapters_for_qa)

    # --- QA STEP 4: Forbidden claims audit (author-specified boundaries) ---
    forbidden_hits = _check_forbidden_claims(ctx, all_chapters_for_qa)

    logger.info("=" * 80)
    logger.info("PHASE 3.5 COMPLETE - QA reports generated")
    logger.info(f"  Reports saved to: {ctx.folders['drafts']}/qa_*.md")
    logger.info("=" * 80)

    rate_limit_delay()

    artifacts = {}
    drafts = ctx.folders.get('drafts') if ctx.folders else None
    if drafts:
        for key, name in [
            ("narrative_report", "qa_narrative_consistency.md"),
            ("voice_report", "qa_voice_unification.md"),
            ("factcheck_report", "qa_factcheck.md"),
            ("forbidden_claims_report", "qa_forbidden_claims.md"),
        ]:
            p = drafts / name
            if p.exists():
                artifacts[key] = str(p)

    return PhaseResult(
        phase="validate",
        status=PhaseStatus.SUCCESS,
        artifacts=artifacts,
        metrics={
            "claims_checked": factcheck_claims,
            "forbidden_claim_hits": len(forbidden_hits),
        },
        duration_seconds=time.time() - phase_start,
    )


def _build_qa_content(ctx: DraftContext) -> str:
    """Build truncated QA review content from chapter files and outputs."""
    try:
        lit_review_content = ""
        methodology_content = ""
        results_content = ""
        discussion_content = ""

        for name, var in [
            ("02_1_literature_review.md", "lit_review"),
            ("02_2_methodology.md", "methodology"),
            ("02_3_analysis_results.md", "results"),
            ("02_4_discussion.md", "discussion"),
        ]:
            fpath = ctx.folders['drafts'] / name
            if fpath.exists():
                content = fpath.read_text(encoding='utf-8')
                if var == "lit_review":
                    lit_review_content = content
                elif var == "methodology":
                    methodology_content = content
                elif var == "results":
                    results_content = content
                elif var == "discussion":
                    discussion_content = content

        return f"""# Complete Draft for QA Review

Topic: {ctx.topic}

## Chapter 1: Introduction (First 1500 chars)
{ctx.intro_output[:1500]}

## Chapter 2: Main Body

### Section 2.1: Literature Review (First 1000 + Last 1000 chars)
{lit_review_content[:1000]}
[... middle content truncated ...]
{lit_review_content[-1000:] if len(lit_review_content) > 1000 else ''}

### Section 2.2: Methodology (First 1000 + Last 1000 chars)
{methodology_content[:1000]}
[... middle content truncated ...]
{methodology_content[-1000:] if len(methodology_content) > 1000 else ''}

### Section 2.3: Analysis & Results (First 1000 + Last 1000 chars)
{results_content[:1000]}
[... middle content truncated ...]
{results_content[-1000:] if len(results_content) > 1000 else ''}

### Section 2.4: Discussion (First 1000 + Last 1000 chars)
{discussion_content[:1000]}
[... middle content truncated ...]
{discussion_content[-1000:] if len(discussion_content) > 1000 else ''}

## Chapter 3: Conclusion (First 1500 chars)
{ctx.conclusion_output[:1500]}

## Chapter 4: Appendices (First 1000 chars)
{ctx.appendix_output[:1000]}
"""
    except Exception as e:
        logger.warning(f"Could not read section files for QA: {e}")
        return f"""# Complete Draft for QA Review (Truncated)

Topic: {ctx.topic}

Introduction: {ctx.intro_output[:1500]}
Main Body: {ctx.body_output[:2000]}
Conclusion: {ctx.conclusion_output[:1500]}
Appendices: {ctx.appendix_output[:1000]}
"""


def _run_thread(ctx: DraftContext, qa_content: str) -> None:
    from utils.agent_runner import run_agent

    try:
        logger.info("[QA 1/3] Running Thread agent - Narrative Consistency Check")
        qa_start = time.time()

        run_agent(
            model=ctx.model,
            name="Thread - Narrative Consistency",
            prompt_path="prompts/03_compose/thread.md",
            user_input=f"""Review the complete draft for narrative consistency.

{qa_content}

**Check for:**
1. Contradictions across sections
2. Fulfilled promises (Introduction \u2192 Conclusion)
3. Proper cross-references
4. Consistent terminology
5. Logical flow between sections

**Focus on Main Body sections 2.1-2.4:**
- Do they reference each other properly?
- Is there narrative continuity?
- Are research gaps from 2.1 addressed in 2.4?""",
            save_to=ctx.folders['drafts'] / "qa_narrative_consistency.md",
            skip_validation=True,
            verbose=ctx.verbose,
            token_tracker=ctx.token_tracker,
            token_stage="thread",
        )

        thread_time = time.time() - qa_start
        logger.info(f"[QA 1/3] \u2705 Thread agent complete in {thread_time:.1f}s")

        if ctx.tracker:
            ctx.tracker.update_phase("writing", progress_percent=78, chapters_count=4, details={"stage": "qa_narrative_complete"})

    except Exception as e:
        logger.warning(f"[QA 1/3] \u26a0\ufe0f  Thread agent failed: {e}")
        logger.warning("Continuing without narrative consistency check...")


def _run_narrator(ctx: DraftContext, qa_content: str) -> None:
    from utils.agent_runner import run_agent

    try:
        logger.info("[QA 2/3] Running Narrator agent - Voice Unification Check")
        qa_start = time.time()

        run_agent(
            model=ctx.model,
            name="Narrator - Voice Unification",
            prompt_path="prompts/03_compose/narrator.md",
            user_input=f"""Review the complete draft for voice consistency.

{qa_content}

**Check for:**
1. Consistent tone (formal, objective, confident)
2. Proper person usage (first/third person)
3. Appropriate tense by section
4. Uniform vocabulary level
5. Consistent hedging language

**Target:** Academic {ctx.academic_level}-level draft
**Citation style:** {ctx.citation_database.citation_style}""",
            save_to=ctx.folders['drafts'] / "qa_voice_unification.md",
            skip_validation=True,
            verbose=ctx.verbose,
            token_tracker=ctx.token_tracker,
            token_stage="narrator",
        )

        narrator_time = time.time() - qa_start
        logger.info(f"[QA 2/3] \u2705 Narrator agent complete in {narrator_time:.1f}s")

        if ctx.tracker:
            ctx.tracker.update_phase("writing", progress_percent=79, chapters_count=4, details={"stage": "qa_narrator_complete"})

    except Exception as e:
        logger.warning(f"[QA 2/3] \u26a0\ufe0f  Narrator agent failed: {e}")
        logger.warning("Continuing without voice unification check...")


def _run_factcheck(ctx: DraftContext, qa_content: str) -> int:
    """Run the FactCheck agent; returns the number of claims verified (0 if skipped)."""
    from utils.agent_runner import run_agent

    if not ctx.config.validation.enable_factcheck:
        logger.info("[QA 3/3] FactCheck disabled (enable_factcheck=False) — skipping")
        if ctx.tracker:
            ctx.tracker.update_phase("writing", progress_percent=80, chapters_count=4, details={"stage": "qa_complete"})
        return 0

    try:
        logger.info("[QA 3/3] Running FactCheck agent - Factual Claim Verification")
        qa_start = time.time()

        gt_protocol = ctx.research_brief.ground_truth_protocol if ctx.research_brief else None
        gt_block = ""
        if gt_protocol:
            gt_block = f"""
**GROUND TRUTH PROTOCOL (author-specified):** {gt_protocol}
Claims that depend on empirical ground truth are only verifiable against this
protocol. Flag any empirical claim that cannot be traced to it as INSUFFICIENT.
"""

        extraction_output = run_agent(
            model=ctx.model,
            name="FactCheck - Claim Extraction",
            prompt_path="prompts/04_validate/factcheck_extract.md",
            user_input=f"Extract all verifiable factual claims from this draft:\n\n{qa_content}{gt_block}",
            skip_validation=True,
            verbose=ctx.verbose,
            token_tracker=ctx.token_tracker,
            token_stage="factcheck_extract",
        )

        from utils.factcheck_verifier import strip_json_fences
        from utils.models import FactCheckClaim
        from pydantic import TypeAdapter

        claims_adapter = TypeAdapter(list[FactCheckClaim])
        validated_claims = claims_adapter.validate_json(strip_json_fences(extraction_output))
        claims = [c.model_dump() for c in validated_claims]
        logger.info(f"[QA 3/3] Extracted {len(claims)} factual claims for verification")

        if claims:
            from utils.factcheck_verifier import FactCheckVerifier

            verifier = FactCheckVerifier(api_key=ctx.config.google_api_key, model=ctx.model)
            results = verifier.verify_claims(claims)

            factcheck_report = verifier.format_report(results)
            factcheck_path = ctx.folders['drafts'] / "qa_factcheck.md"
            factcheck_path.write_text(factcheck_report, encoding='utf-8')

            contradicted_count = sum(1 for r in results if r["verdict"] == "CONTRADICTED")
            supported_count = sum(1 for r in results if r["verdict"] == "SUPPORTED")

            factcheck_time = time.time() - qa_start
            logger.info(
                f"[QA 3/3] \u2705 FactCheck complete in {factcheck_time:.1f}s \u2014 "
                f"{len(claims)} claims checked, {supported_count} supported, "
                f"{contradicted_count} issues found"
            )
        else:
            logger.info("[QA 3/3] No factual claims extracted \u2014 skipping verification")

        if ctx.tracker:
            ctx.tracker.update_phase("writing", progress_percent=80, chapters_count=4, details={"stage": "qa_complete"})

        return len(claims)

    except json.JSONDecodeError as e:
        logger.warning(f"[QA 3/3] \u26a0\ufe0f  FactCheck claim extraction returned invalid JSON: {e}")
        logger.warning("Continuing without fact-check verification...")
        return 0
    except Exception as e:
        logger.warning(f"[QA 3/3] \u26a0\ufe0f  FactCheck agent failed: {e}")
        logger.warning("Continuing without fact-check verification...")
        return 0


def _check_forbidden_claims(ctx: DraftContext, qa_content: str) -> List[Tuple[str, float, List[str]]]:
    """
    Audit the draft against the author's forbidden-claims list.

    Pure-text keyword matching (see match_forbidden_claims). Writes
    qa_forbidden_claims.md and emits events; never raises.
    """
    forbidden = ctx.research_brief.forbidden_claims if ctx.research_brief else []
    if not forbidden:
        return []

    logger.info(f"[QA 4] Auditing {len(forbidden)} forbidden claims (author boundaries)")
    hits = match_forbidden_claims(qa_content, forbidden)

    report_lines = [
        "# Forbidden Claims Audit",
        "",
        f"The author specified {len(forbidden)} claims that must NOT appear in the paper.",
        "",
    ]
    if hits:
        report_lines.append(f"**\u26a0\ufe0f {len(hits)} potential violation(s) detected:**")
        for claim, ratio, matched in hits:
            report_lines.append(f"\n- `{claim}` — {ratio:.0%} keyword overlap (matched: {', '.join(matched)})")
        report_lines.append("\nManual review required: rewrite these passages so the forbidden claim is not made.")
    else:
        report_lines.append("\u2705 No forbidden claims detected.")

    report = "\n".join(report_lines)
    try:
        (ctx.folders['drafts'] / "qa_forbidden_claims.md").write_text(report, encoding='utf-8')
    except Exception as e:
        logger.warning(f"[QA 4] Could not write forbidden-claims report: {e}")

    if hits:
        logger.warning(f"[QA 4] \u26a0\ufe0f  {len(hits)} forbidden-claim violation(s) — see qa_forbidden_claims.md")
        if ctx.verbose:
            print(f"   \u26a0\ufe0f  Forbidden claims audit: {len(hits)} violation(s)")
        if ctx.tracker:
            ctx.tracker.log_activity(
                f"\u26a0\ufe0f  Forbidden claim violations: {len(hits)}",
                event_type="warning", phase="writing",
            )
    else:
        logger.info("[QA 4] \u2705 No forbidden claims detected")
        if ctx.verbose:
            print("   \u2705 Forbidden claims audit passed")

    if getattr(ctx, "event_bus", None):
        from protocols import PhaseEventType
        ctx.event_bus.emit(
            PhaseEventType.FORBIDDEN_CLAIM_DETECTED if hits else PhaseEventType.VALIDATION_ISSUE,
            phase="validate",
            data={"violations": [h[0] for h in hits], "checked": len(forbidden)},
        )

    return hits
