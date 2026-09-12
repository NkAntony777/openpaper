#!/usr/bin/env python3
"""
ABOUTME: Section writing-task prompt builder for the pi harness (design doc §5.1).
ABOUTME: Pulls the section's word target from checkpoint.json and points the agent at the
ABOUTME: paper map / research material, so the model drives the read -> write -> score loop.
"""

import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_tools.common import SECTION_FILES, parse_target_max, read_checkpoint

OUTLINE_HINTS = [
    "drafts/00_formatted_outline.md",
    "drafts/00_outline.md",
]


def _existing(root: Path, rels: List[str]) -> List[str]:
    return [rel for rel in rels if (root / rel).exists()]


def build_section_prompt(root, section: str) -> str:
    """Build the English writing-task prompt for one section. Raises ValueError for
    unknown sections."""
    root = Path(root)
    if section not in SECTION_FILES:
        valid = ", ".join(sorted(SECTION_FILES))
        raise ValueError(f"unknown section '{section}' (valid: {valid})")

    meta = SECTION_FILES[section]
    ckpt = read_checkpoint(root) or {}
    topic = ckpt.get("topic") or "(topic unknown — read the outline first)"
    level = ckpt.get("academic_level", "unknown")
    style = ckpt.get("citation_style", "apa")
    lang = ckpt.get("language", "en")
    raw_target = (ckpt.get("word_targets") or {}).get(meta["wt_key"])
    target = parse_target_max(raw_target)

    outline_files = _existing(root, OUTLINE_HINTS)
    research_files = _existing(root, [
        "research/combined_research.md",
        "research/research_gaps.md",
    ])
    has_papers_dir = (root / "research" / "papers").is_dir()
    has_bibliography = (root / "research" / "bibliography.json").exists()
    has_summary = (root / "drafts" / "citation_summary.md").exists()

    target_line = (
        f"- Target length: {target} words. The write is REJECTED below ~70% of this, so "
        f"write the full section in one write_section call."
        if target
        else "- No word target found in checkpoint.json — match the depth implied by the outline "
             "and the neighboring sections."
    )

    material = [
        "- `AGENTS.md` (the paper map: outline digest, per-section status, bibliography stats, "
        "writing discipline) — read it first.",
    ]
    if outline_files:
        material.append(f"- The outline: `{outline_files[0]}`.")
    else:
        material.append(
            "- The outline: none found on disk yet — derive the section's role from the "
            "research material and AGENTS.md."
        )
    if research_files:
        for rel in research_files:
            material.append(f"- `{rel}`.")
    if has_papers_dir:
        material.append("- The per-paper notes `research/papers/*.md` covering this section's "
                        "subtopics (list them with the `ls` tool, then read the relevant ones).")
    if has_bibliography:
        material.append("- The citation ledger `research/bibliography.json` — the ONLY source of "
                        "citable ids.")
    if has_summary:
        material.append("- `drafts/citation_summary.md` for a readable view of the ledger.")

    forbidden = []
    brief = ckpt.get("research_brief") or {}
    if isinstance(brief, dict):
        forbidden = [str(c).strip() for c in (brief.get("forbidden_claims") or []) if str(c).strip()]

    lines = [
        f"Write the `{section}` section of an academic paper on: {topic}",
        "",
        f"Paper context: academic level={level}, citation style={style}, language={lang}.",
        f"The section will be saved as `{meta['file']}`.",
        target_line,
        "",
        "Material (read it with read_artifact — never write from memory):",
        *material,
        "",
        "Protocol:",
        "1. Read the material above. Ground every paragraph in the research notes; note which "
        "cite_XXX ids support which claims.",
        "2. Cite ONLY cite_XXX ids present in research/bibliography.json. Missing a source? Call "
        "search_literature FIRST, then cite the new ids it returns. Never invent citations.",
        "3. Write the complete section with write_section (full markdown in `content`, every "
        "cited id listed in citations_used, AND a 1-2 sentence `summary` of the section's core "
        "claims and key terms — the summary is the global review's input, never skip it).",
        "4. Self-check with score_draft (scope=section). Fix every reported issue: revise_section "
        "for wording/structure/completeness, search_literature + rewrite for thin evidence. "
        "Re-score after fixes until no high-severity issues remain.",
        "5. Record the section's 3-5 most important factual claims with manage_claims "
        "(action=record). Spot-check them with manage_claims action=verify (or "
        "verify_claims for a one-off). CONTRADICTED verdicts include a find_replace "
        "list — feed it to revise_section, then manage_claims action=resolve "
        "(status=revised or deleted). Unresolved CONTRADICTED claims fail the paper "
        "finish gate.",
        "6. If writing reveals the outline is wrong, use write_outline (merge=true) "
        "before continuing — do not silently drift from the plan.",
        "7. Finish with a plain-text report containing exactly: the file path written, the final "
        "word count, the number of distinct cite_XXX ids used, and the final score_draft score "
        "with any issues you could not resolve.",
    ]
    if forbidden:
        lines.extend([
            "",
            "FORBIDDEN claims (must NOT appear anywhere in this section — the finish gate "
            "scans for them):",
            *[f"- {c}" for c in forbidden],
        ])
    return "\n".join(lines) + "\n"


def build_fix_prompt(root, section: str, issues: List[Dict]) -> str:
    """Build the targeted-fix prompt for one section: address global-review findings with
    revise_section, re-score, and report what was fixed. Raises ValueError for unknown
    sections."""
    root = Path(root)
    if section not in SECTION_FILES:
        valid = ", ".join(sorted(SECTION_FILES))
        raise ValueError(f"unknown section '{section}' (valid: {valid})")
    meta = SECTION_FILES[section]

    lines = [
        f"Fix global-review findings in the `{section}` section of the paper in this directory.",
        "",
        "Material (read it with read_artifact — never fix from memory):",
        "- `AGENTS.md` (the paper map) — for outline context and neighboring-section state.",
        f"- The current draft `{meta['file']}` — the file you are fixing.",
        "",
        "Findings assigned to this section (from global_issues.md):",
    ]
    for i, it in enumerate(issues, 1):
        lines.append(f"{i}. [{it.get('severity', 'medium')}] {it.get('issue', '').strip()}")
        fix = (it.get("fix") or "").strip()
        if fix:
            lines.append(f"   Suggested fix: {fix}")
    lines += [
        "",
        "Protocol:",
        "1. Read the current draft and AGENTS.md.",
        "2. Address every finding with revise_section — prefer find_replace pairs for precise "
        "unique-match edits; use free-text instructions for broader rewrites. Never introduce "
        "citations that are not in research/bibliography.json.",
        "3. Re-check with score_draft (scope=section): the section must still pass its "
        "guardrails (word floor, no placeholders). Fix regressions before finishing. If a "
        "finding is a factual error, resolve the matching claims-ledger entry "
        "(manage_claims action=resolve).",
        "4. Finish with a plain-text report: for each finding, FIXED or NOT FIXED with a "
        "one-line reason, plus the final score_draft passed state.",
    ]
    return "\n".join(lines) + "\n"
