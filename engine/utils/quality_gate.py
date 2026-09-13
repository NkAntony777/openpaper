#!/usr/bin/env python3
"""
ABOUTME: Quality gate for draft output scoring
ABOUTME: Scores draft quality after compose phase, enables early exit or warnings
ABOUTME: Pure-function core (score_texts) is the agent-tool entry point; ctx-based
ABOUTME: API (score_draft_quality / run_quality_gate) delegates for backward compat.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Tuple

if TYPE_CHECKING:
    from phases.context import DraftContext

logger = logging.getLogger(__name__)


@dataclass
class QualityIssue:
    """Structured diagnostic for a single quality problem."""

    section: str  # e.g. 'introduction', 'body', 'citations', 'structure'
    metric: str  # e.g. 'word_count', 'unique_citations', 'placeholder'
    actual: str  # observed value (human-readable)
    target: str  # expected value (human-readable)
    severity: str  # 'warning' | 'error'
    message: str  # same text as appended to QualityScore.issues


@dataclass
class QualityScore:
    """Quality assessment result."""

    total_score: int  # 0-100
    word_count_score: int  # 0-25
    citation_score: int  # 0-25
    completeness_score: int  # 0-25
    structure_score: int  # 0-25
    issues: List[str]
    passed: bool
    structured_issues: List[QualityIssue] = field(default_factory=list)


# Minimum word targets per academic level (used by word count scoring)
MIN_WORD_TARGETS = {
    "research_paper": {"intro": 400, "body": 1500, "conclusion": 300},
    "bachelor": {"intro": 1000, "body": 5000, "conclusion": 600},
    "master": {"intro": 1500, "body": 10000, "conclusion": 1000},
    "phd": {"intro": 2500, "body": 20000, "conclusion": 2000},
}


# ---------------------------------------------------------------------------
# Pure scoring cores (no DraftContext dependency — safe for agent tools)
# ---------------------------------------------------------------------------


def _core_word_count(
    intro_words: int, body_words: int, conclusion_words: int, academic_level: str
) -> Tuple[int, List[QualityIssue]]:
    """Score word counts (25 points). Pure: takes counts, not ctx."""
    score = 0
    issues: List[QualityIssue] = []
    targets = MIN_WORD_TARGETS.get(academic_level, MIN_WORD_TARGETS["master"])

    if intro_words >= targets["intro"]:
        score += 8
    elif intro_words >= targets["intro"] * 0.5:
        score += 4
        issues.append(
            QualityIssue(
                "introduction",
                "word_count",
                str(intro_words),
                str(targets["intro"]),
                "warning",
                f"Introduction short: {intro_words} words (target: {targets['intro']})",
            )
        )
    else:
        issues.append(
            QualityIssue(
                "introduction",
                "word_count",
                str(intro_words),
                str(targets["intro"]),
                "error",
                f"Introduction very short: {intro_words} words (target: {targets['intro']})",
            )
        )

    if body_words >= targets["body"]:
        score += 12
    elif body_words >= targets["body"] * 0.5:
        score += 6
        issues.append(
            QualityIssue(
                "body",
                "word_count",
                str(body_words),
                str(targets["body"]),
                "warning",
                f"Body short: {body_words} words (target: {targets['body']})",
            )
        )
    else:
        issues.append(
            QualityIssue(
                "body",
                "word_count",
                str(body_words),
                str(targets["body"]),
                "error",
                f"Body very short: {body_words} words (target: {targets['body']})",
            )
        )

    if conclusion_words >= targets["conclusion"]:
        score += 5
    elif conclusion_words >= targets["conclusion"] * 0.5:
        score += 2
        issues.append(
            QualityIssue(
                "conclusion",
                "word_count",
                str(conclusion_words),
                str(targets["conclusion"]),
                "warning",
                f"Conclusion short: {conclusion_words} words (target: {targets['conclusion']})",
            )
        )
    else:
        issues.append(
            QualityIssue(
                "conclusion",
                "word_count",
                str(conclusion_words),
                str(targets["conclusion"]),
                "error",
                f"Conclusion very short: {conclusion_words} words (target: {targets['conclusion']})",
            )
        )

    return score, issues


def _core_citations(all_text: str, min_citations: int) -> Tuple[int, List[QualityIssue]]:
    """Score citation usage (25 points). Pure: takes text, not ctx."""
    score = 0
    issues: List[QualityIssue] = []

    citation_refs = re.findall(r"\{cite_\d+\}", all_text)
    unique_citations = len(set(citation_refs))
    total_citations = len(citation_refs)

    if unique_citations >= min_citations:
        score += 15
    elif unique_citations >= min_citations * 0.5:
        score += 8
        issues.append(
            QualityIssue(
                "citations",
                "unique_citations",
                str(unique_citations),
                str(min_citations),
                "warning",
                f"Few unique citations: {unique_citations} (target: {min_citations})",
            )
        )
    else:
        issues.append(
            QualityIssue(
                "citations",
                "unique_citations",
                str(unique_citations),
                str(min_citations),
                "error",
                f"Very few citations: {unique_citations} (target: {min_citations})",
            )
        )

    word_count = _count_words(all_text)
    expected_density = max(1, word_count // 500)
    if total_citations >= expected_density:
        score += 10
    elif total_citations >= expected_density * 0.5:
        score += 5
        issues.append(
            QualityIssue(
                "citations",
                "citation_density",
                f"{total_citations} refs / {word_count} words",
                f">= {expected_density} refs",
                "warning",
                f"Low citation density: {total_citations} refs in {word_count} words",
            )
        )
    else:
        issues.append(
            QualityIssue(
                "citations",
                "citation_density",
                f"{total_citations} refs / {word_count} words",
                f">= {expected_density} refs",
                "error",
                f"Very low citation density: {total_citations} refs in {word_count} words",
            )
        )

    return score, issues


def _core_completeness(sections: List[Tuple[str, str]]) -> Tuple[int, List[QualityIssue]]:
    """Score section completeness (25 points). Pure: takes (name, content) pairs."""
    score = 0
    issues: List[QualityIssue] = []

    for name, content in sections:
        key = name.lower().replace(" ", "_")
        stripped = (content or "").strip()
        if stripped and len(stripped) > 100:
            score += 5
        elif stripped:
            score += 2
            issues.append(
                QualityIssue(
                    key,
                    "completeness",
                    f"{len(stripped)} chars",
                    "> 100 chars",
                    "warning",
                    f"{name} section too brief",
                )
            )
        else:
            issues.append(
                QualityIssue(
                    key, "completeness", "missing", "present", "error", f"Missing {name} section"
                )
            )

    return score, issues


STRUCTURE_ERROR_PATTERNS = [
    (r"\{cite_MISSING[^}]*\}", "placeholder", "Contains {cite_MISSING} placeholders"),
    (r"TODO", "placeholder", "Contains TODO markers"),
    (r"\[INSERT\]", "placeholder", "Contains [INSERT] placeholders"),
    (r"Lorem ipsum", "placeholder", "Contains Lorem ipsum placeholder text"),
]


def _core_structure(all_text: str) -> Tuple[int, List[QualityIssue]]:
    """Score markdown structure (25 points). Pure: takes text, not ctx."""
    score = 0
    issues: List[QualityIssue] = []

    headers = re.findall(r"^#{1,3}\s+.+$", all_text, re.MULTILINE)
    if len(headers) >= 5:
        score += 10
    elif len(headers) >= 2:
        score += 5
        issues.append(
            QualityIssue(
                "structure",
                "headers",
                str(len(headers)),
                ">= 5",
                "warning",
                f"Few section headers: {len(headers)}",
            )
        )
    else:
        issues.append(
            QualityIssue(
                "structure",
                "headers",
                str(len(headers)),
                ">= 5",
                "error",
                "Missing section headers",
            )
        )

    paragraphs = all_text.split("\n\n")
    paragraphs = [p for p in paragraphs if len(p.strip()) > 50]
    if len(paragraphs) >= 10:
        score += 5
    elif len(paragraphs) >= 5:
        score += 2
        issues.append(
            QualityIssue(
                "structure",
                "paragraphs",
                str(len(paragraphs)),
                ">= 10",
                "warning",
                f"Few paragraphs: {len(paragraphs)}",
            )
        )
    else:
        issues.append(
            QualityIssue(
                "structure",
                "paragraphs",
                str(len(paragraphs)),
                ">= 10",
                "error",
                "Very few paragraphs",
            )
        )

    deductions = 0
    for pattern, metric, message in STRUCTURE_ERROR_PATTERNS:
        if re.search(pattern, all_text, re.IGNORECASE):
            deductions += 2
            issues.append(QualityIssue("structure", metric, "found", "absent", "error", message))

    score += max(0, 10 - deductions)

    return score, issues


def score_texts(
    texts: Dict[str, str],
    academic_level: str = "master",
    min_citations: int = 10,
) -> QualityScore:
    """
    Score draft quality from raw section texts. Pure function — no DraftContext.

    Args:
        texts: Mapping with keys 'introduction', 'body', 'conclusion' (scored for
            word count / citations / structure) and optionally 'literature_review',
            'methodology', 'results', 'discussion' (scored for completeness).
            Missing keys are treated as empty.
        academic_level: 'research_paper' | 'bachelor' | 'master' | 'phd'
        min_citations: Minimum unique citations expected in the draft.

    Returns:
        QualityScore with both human-readable issues and structured_issues.
    """
    introduction = texts.get("introduction", "") or ""
    body = texts.get("body", "") or ""
    conclusion = texts.get("conclusion", "") or ""
    all_text = introduction + body + conclusion

    word_count_score, wc_issues = _core_word_count(
        _count_words(introduction), _count_words(body), _count_words(conclusion), academic_level
    )
    citation_score, cit_issues = _core_citations(all_text, min_citations)
    completeness_score, comp_issues = _core_completeness(
        [
            ("Introduction", introduction),
            ("Literature Review", texts.get("literature_review", "") or ""),
            ("Methodology", texts.get("methodology", "") or ""),
            ("Results", texts.get("results", "") or ""),
            ("Conclusion", conclusion),
        ]
    )
    structure_score, struct_issues = _core_structure(all_text)

    structured = wc_issues + cit_issues + comp_issues + struct_issues
    total_score = word_count_score + citation_score + completeness_score + structure_score
    passed = total_score >= 50  # Minimum passing score

    return QualityScore(
        total_score=total_score,
        word_count_score=word_count_score,
        citation_score=citation_score,
        completeness_score=completeness_score,
        structure_score=structure_score,
        issues=[i.message for i in structured],
        passed=passed,
        structured_issues=structured,
    )


# ---------------------------------------------------------------------------
# DraftContext-based API (backward compatible — delegates to the pure cores)
# ---------------------------------------------------------------------------


def score_draft_quality(ctx: "DraftContext") -> QualityScore:
    """
    Score draft quality after compose phase.

    Scoring breakdown (100 points total):
    - Word count: 25 points (meets target lengths)
    - Citations: 25 points (proper citation density)
    - Completeness: 25 points (all sections present)
    - Structure: 25 points (proper markdown structure)

    Args:
        ctx: DraftContext with compose outputs

    Returns:
        QualityScore with breakdown and pass/fail status
    """
    return score_texts(
        texts={
            "introduction": ctx.intro_output,
            "body": ctx.body_output,
            "conclusion": ctx.conclusion_output,
            "literature_review": getattr(ctx, "lit_review_output", ""),
            "methodology": getattr(ctx, "methodology_output", ""),
            "results": getattr(ctx, "results_output", ""),
            "discussion": getattr(ctx, "discussion_output", ""),
        },
        academic_level=ctx.academic_level,
        min_citations=ctx.word_targets.get("min_citations", 10),
    )


def _count_words(text: str) -> int:
    """Count words in text."""
    return len(text.split()) if text else 0


def _score_word_count(ctx: "DraftContext", issues: List[str]) -> int:
    """Score based on word count targets."""
    score, found = _core_word_count(
        _count_words(ctx.intro_output),
        _count_words(ctx.body_output),
        _count_words(ctx.conclusion_output),
        ctx.academic_level,
    )
    issues.extend(i.message for i in found)
    return score


def _score_citations(ctx: "DraftContext", issues: List[str]) -> int:
    """Score based on citation usage."""
    all_text = ctx.intro_output + ctx.body_output + ctx.conclusion_output
    score, found = _core_citations(all_text, ctx.word_targets.get("min_citations", 10))
    issues.extend(i.message for i in found)
    return score


def _score_completeness(ctx: "DraftContext", issues: List[str]) -> int:
    """Score based on section completeness."""
    score, found = _core_completeness(
        [
            ("Introduction", ctx.intro_output),
            ("Literature Review", ctx.lit_review_output),
            ("Methodology", ctx.methodology_output),
            ("Results", ctx.results_output),
            ("Conclusion", ctx.conclusion_output),
        ]
    )
    issues.extend(i.message for i in found)
    return score


def _score_structure(ctx: "DraftContext", issues: List[str]) -> int:
    """Score based on markdown structure."""
    all_text = ctx.intro_output + ctx.body_output + ctx.conclusion_output
    score, found = _core_structure(all_text)
    issues.extend(i.message for i in found)
    return score


def run_quality_gate(ctx: "DraftContext", strict: bool = False) -> QualityScore:
    """
    Run quality gate after compose phase.

    Args:
        ctx: DraftContext with compose outputs
        strict: If True, raise error on low quality. If False, log warning.

    Returns:
        QualityScore result

    Raises:
        ValueError: If strict=True and quality score < 50
    """
    logger.info("Running quality gate assessment...")

    result = score_draft_quality(ctx)

    logger.info(f"Quality Score: {result.total_score}/100")
    logger.info(f"  Word Count: {result.word_count_score}/25")
    logger.info(f"  Citations:  {result.citation_score}/25")
    logger.info(f"  Completeness: {result.completeness_score}/25")
    logger.info(f"  Structure:  {result.structure_score}/25")

    if result.issues:
        logger.info(f"Issues found: {len(result.issues)}")
        for issue in result.issues:
            logger.warning(f"  - {issue}")

    if not result.passed:
        msg = f"Quality gate failed: score {result.total_score}/100 (minimum: 50)"
        if strict:
            raise ValueError(msg)
        else:
            logger.warning(msg)
            logger.warning("Continuing despite low quality score (strict=False)")
    else:
        logger.info(f"Quality gate passed: {result.total_score}/100")

    return result
