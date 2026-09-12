#!/usr/bin/env python3
"""
ABOUTME: M3 finish-gate — forbidden_claims scan, unresolved CONTRADICTED claims, and
ABOUTME: citation authenticity. Pure offline (no LLM, no network). Used by run_paper
ABOUTME: and `opendraft harness section` after the agent settles, and by the eval suite.
"""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from agent_tools.claims_ledger import unresolved_contradictions
from agent_tools.common import SECTION_FILES, bibliography_ids, read_checkpoint

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
    keywords hit AND ≥2 keywords (single-keyword claims: case-insensitive substring)."""
    hits: List[Dict] = []
    draft_words = set(_keywords(text))
    draft_l = (text or "").lower()
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


def run_finish_acceptance(root, write_forbidden_report: bool = True) -> FinishAcceptance:
    """Driver-side T8 finish gate. Never raises."""
    root = Path(root)
    unresolved = unresolved_contradictions(root)
    claims_clean = len(unresolved) == 0
    hits = scan_forbidden_claims(root) if write_forbidden_report else match_forbidden_claims(
        _all_section_text(root), forbidden_claims_from_checkpoint(root)
    )
    cites = citation_authenticity(root)

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
    if cites["cite_missing"]:
        gaps.append(f"{cites['cite_missing']} {{cite_MISSING}} token(s) remain")
    if cites["unknown"]:
        gaps.append(
            f"{len(cites['unknown'])} cite id(s) not in bibliography.json: "
            + ", ".join(cites["unknown"][:8])
        )

    passed = claims_clean and not hits and cites["cite_missing"] == 0 and not cites["unknown"]
    return FinishAcceptance(
        passed=passed,
        claims_clean=claims_clean,
        unresolved_contradicted=unresolved,
        forbidden_hits=hits,
        citation_rate=cites["rate"],
        unknown_citations=cites["unknown"],
        cite_missing=cites["cite_missing"],
        gaps=gaps,
    )
