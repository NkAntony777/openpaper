#!/usr/bin/env python3
"""
ABOUTME: M3 finish-gate — forbidden_claims scan (negation-aware), unresolved CONTRADICTED
ABOUTME: claims, citation authenticity, section presence + word floors, and an optional
ABOUTME: full-score floor. Pure offline (no LLM, no network). Used by run_paper and
ABOUTME: `opendraft harness section` after the agent settles, and by the eval suite.
"""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from agent_tools.claims_ledger import unresolved_contradictions
from agent_tools.common import SECTION_FILES, bibliography_ids, read_checkpoint, word_target_max
from agent_tools.write_section import WORD_FLOOR_RATIO

CITE_REF_RE = re.compile(r"\{cite_(\d+)\}")
CITE_MISSING_RE = re.compile(r"\{cite_MISSING[^}]*\}", re.IGNORECASE)
WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
FORBIDDEN_REPORT_REL = "qa_forbidden_claims.md"

# Stopwords dropped before keyword-overlap matching so "we prove that X" doesn't
# fire just because "we"/"that" appear in the draft.
_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "that", "the", "this", "to", "we", "with",
}

# Sentences carrying these cues are refutations/negations ("we do not claim X",
# "contrary to X, ...") — exempt from the forbidden scan so the gate cannot fail
# a paper that explicitly refuses a forbidden claim.
_NEGATION_CUES = re.compile(
    r"\b(no|not|never|without|refute[sd]?\b|reject[sd]?\b|rejecting|challeng"
    r"e[sd]?\b|challenging|contrary to|do not|does not|did not)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"[\n\r.!?]+")

FLOOR_RATIO = WORD_FLOOR_RATIO  # mirror of write_section's guardrail ratio

FORBIDDEN_OVERLAP = 0.6
FORBIDDEN_MIN_HITS = 2


@dataclass
class FinishAcceptance:
    passed: bool
    claims_clean: bool
    unresolved_contradicted: List[Dict] = field(default_factory=list)
    forbidden_hits: List[Dict] = field(default_factory=list)
    citation_rate: float = 1.0
    unknown_citations: List[str] = field(default_factory=list)
    cite_missing: int = 0
    missing_sections: List[str] = field(default_factory=list)
    thin_sections: List[Dict] = field(default_factory=list)
    quality_gap: Optional[str] = None
    gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


def forbidden_claims_from_checkpoint(root) -> List[str]:
    ckpt = read_checkpoint(root) or {}
    brief = ckpt.get("research_brief") or {}
    if not isinstance(brief, dict):
        return []
    raw = brief.get("forbidden_claims") or brief.get("negative_claims") or []
    if not isinstance(raw, list):
        return []
    return [str(c).strip() for c in raw if str(c).strip()]


def _keywords(text: str) -> List[str]:
    return [w.lower() for w in WORD_RE.findall(text or "") if w.lower() not in _STOP]


def match_forbidden_claims(text: str, forbidden: List[str]) -> List[Dict]:
    """Keyword-overlap matcher (OPTIMIZATION-IMPLEMENTATION §3): ≥60% of non-stop
    keywords hit AND ≥2 keywords (single-keyword claims: case-insensitive substring).
    Negated/refuting sentences are exempt — the gate must not punish a paper that
    explicitly refuses a forbidden claim."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text or "") if s.strip()]
    scan_target = "\n".join(
        s for s in sentences if not _NEGATION_CUES.search(s)
    )
    hits: List[Dict] = []
    draft_words = set(_keywords(scan_target))
    draft_l = scan_target.lower()
    for claim in forbidden:
        keys = _keywords(claim)
        if not keys:
            continue
        if len(keys) == 1:
            if keys[0] in draft_l:
                hits.append({"claim": claim, "overlap": 1.0, "matched_words": keys})
            continue
        matched = [w for w in keys if w in draft_words]
        overlap = len(matched) / len(keys)
        if overlap >= FORBIDDEN_OVERLAP and len(matched) >= FORBIDDEN_MIN_HITS:
            hits.append({
                "claim": claim,
                "overlap": round(overlap, 2),
                "matched_words": matched,
            })
    return hits


def _all_section_text(root: Path) -> str:
    parts = []
    for meta in SECTION_FILES.values():
        f = Path(root) / meta["file"]
        if f.exists():
            parts.append(f.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def scan_forbidden_claims(root) -> List[Dict]:
    forbidden = forbidden_claims_from_checkpoint(root)
    if not forbidden:
        return []
    hits = match_forbidden_claims(_all_section_text(root), forbidden)
    report = Path(root) / FORBIDDEN_REPORT_REL
    lines = [
        "# Forbidden claims audit",
        "",
        f"Scanned {len(forbidden)} forbidden claim(s); hits: {len(hits)}",
        "",
    ]
    if hits:
        for h in hits:
            lines.append(f"- HIT ({h['overlap']:.0%}): {h['claim']}")
            lines.append(f"  matched: {', '.join(h.get('matched_words') or [])}")
    else:
        lines.append("(none)")
    lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")
    return hits


def citation_authenticity(root) -> Dict:
    """Fraction of {cite_NNN} ids that exist in bibliography.json. Vacuous 1.0 when
    the draft cites nothing. {cite_MISSING...} is counted separately and always dirty."""
    known = bibliography_ids(root)
    used: List[str] = []
    missing_tokens = 0
    for meta in SECTION_FILES.values():
        f = Path(root) / meta["file"]
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        missing_tokens += len(CITE_MISSING_RE.findall(text))
        for n in CITE_REF_RE.findall(text):
            used.append(f"cite_{n}")
    unknown = sorted({c for c in used if c not in known})
    total = len(used)
    rate = 1.0 if total == 0 else (total - len([c for c in used if c not in known])) / total
    return {
        "rate": rate,
        "used": total,
        "unknown": unknown,
        "cite_missing": missing_tokens,
    }


def planned_sections(root) -> List[str]:
    """Sections the checkpoint's word_targets promise (non-numeric keys like
    'min_citations' are filtered out)."""
    ckpt = read_checkpoint(root) or {}
    targets = ckpt.get("word_targets") or {}
    if not isinstance(targets, dict):
        return []
    return [s for s in targets
            if s in SECTION_FILES and word_target_max(root, SECTION_FILES[s]["wt_key"]) > 0]


def section_completeness(root) -> "tuple[List[str], List[Dict]]":
    """(missing_sections, thin_sections) against the plan: a required section must
    exist and meet the same word floor write_section enforces (FLOOR_RATIO × target)."""
    missing: List[str] = []
    thin: List[Dict] = []
    root = Path(root)
    for section in planned_sections(root):
        target = word_target_max(root, SECTION_FILES[section]["wt_key"])
        f = root / SECTION_FILES[section]["file"]
        if not f.exists():
            missing.append(section)
            continue
        words = len(f.read_text(encoding="utf-8").split())
        floor = int(target * FLOOR_RATIO)
        if words < floor:
            thin.append({"section": section, "words": words, "floor": floor,
                         "target": target})
    return missing, thin


def run_finish_acceptance(
    root,
    min_full_score: Optional[int] = None,
    full_score: Optional[int] = None,
    write_forbidden_report: bool = True,
) -> FinishAcceptance:
    """Driver-side T8 finish gate. Never raises.

    Checks (all offline): unresolved CONTRADICTED claims, forbidden-claim hits
    (negation-aware), citation authenticity, every planned section present and above
    the word floor, and — when min_full_score is given — the full quality score."""
    root = Path(root)
    unresolved = unresolved_contradictions(root)
    claims_clean = len(unresolved) == 0
    hits = scan_forbidden_claims(root) if write_forbidden_report else match_forbidden_claims(
        _all_section_text(root), forbidden_claims_from_checkpoint(root)
    )
    cites = citation_authenticity(root)
    missing, thin = section_completeness(root)

    quality_gap: Optional[str] = None
    if min_full_score is not None:
        if full_score is None:
            quality_gap = f"full quality score unavailable (require >= {min_full_score})"
        elif full_score < min_full_score:
            quality_gap = f"full quality score {full_score} < required {min_full_score}"

    gaps: List[str] = []
    if unresolved:
        ids = ", ".join(e.get("id") or e.get("claim", "?") for e in unresolved[:8])
        gaps.append(
            f"{len(unresolved)} CONTRADICTED claim(s) unhandled (revise then "
            f"manage_claims action=resolve, or delete): {ids}"
        )
    if hits:
        gaps.append(
            f"{len(hits)} forbidden claim(s) appear in the draft: "
            + "; ".join(h["claim"] for h in hits[:5])
        )
    if missing:
        gaps.append(
            f"{len(missing)} planned section(s) missing on disk: {', '.join(missing)}"
        )
    for t in thin[:5]:
        gaps.append(
            f"section {t['section']} below word floor: {t['words']} words "
            f"(floor {t['floor']}, target {t['target']})"
        )
    if len(thin) > 5:
        gaps.append(f"... and {len(thin) - 5} more below-floor section(s)")
    if cites["cite_missing"]:
        gaps.append(f"{cites['cite_missing']} {{cite_MISSING}} token(s) remain")
    if cites["unknown"]:
        gaps.append(
            f"{len(cites['unknown'])} cite id(s) not in bibliography.json: "
            + ", ".join(cites["unknown"][:8])
        )
    if quality_gap:
        gaps.append(quality_gap)

    passed = (claims_clean and not hits and not missing and not thin
              and cites["cite_missing"] == 0 and not cites["unknown"]
              and quality_gap is None)
    return FinishAcceptance(
        passed=passed,
        claims_clean=claims_clean,
        unresolved_contradicted=unresolved,
        forbidden_hits=hits,
        citation_rate=cites["rate"],
        unknown_citations=cites["unknown"],
        cite_missing=cites["cite_missing"],
        missing_sections=missing,
        thin_sections=thin,
        quality_gap=quality_gap,
        gaps=gaps,
    )
