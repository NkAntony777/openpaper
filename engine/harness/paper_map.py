#!/usr/bin/env python3
"""
ABOUTME: AGENTS.md paper map generator (design doc §7.1).
ABOUTME: The map is the paper-level "repo map": gives the agent a <=1k-token view of the
ABOUTME: whole paper so it can decide where to drill in via read_artifact.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from agent_tools.common import SECTION_FILES, read_checkpoint

OUTLINE_CANDIDATES = [
    "drafts/00_formatted_outline.md",
    "drafts/00_outline.md",
]

BIBLIOGRAPHY_REL = "research/bibliography.json"


def _read(root: Path, rel: str) -> str:
    p = root / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _word_count(text: str) -> int:
    return len(text.split()) if text else 0


def _first_meaningful_lines(text: str, max_lines: int = 25, max_chars: int = 1500) -> str:
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    snippet = "\n".join(lines[:max_lines])
    return snippet[:max_chars]


def _bibliography_stats(root: Path) -> Dict:
    p = root / BIBLIOGRAPHY_REL
    if not p.exists():
        return {"count": 0, "years": ""}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"count": 0, "years": "(bibliography.json unreadable)"}
    cits = data.get("citations", [])
    years = sorted({c.get("year") for c in cits if isinstance(c.get("year"), int)})
    span = f"{years[0]}-{years[-1]}" if years else ""
    return {"count": len(cits), "years": span}


def _section_rows(root: Path) -> List[Dict]:
    rows = []
    for name, meta in SECTION_FILES.items():
        text = _read(root, meta["file"])
        rows.append({
            "section": name,
            "file": meta["file"],
            "words": _word_count(text),
            "status": "written" if text.strip() else "pending",
        })
    return rows


def generate_paper_map(root: Path) -> str:
    """Render AGENTS.md content for the output directory. Pure read, no side effects."""
    root = Path(root)
    ckpt = read_checkpoint(root) or {}
    topic = ckpt.get("topic") or "(topic unknown — run research first)"

    outline = ""
    for cand in OUTLINE_CANDIDATES:
        outline = _read(root, cand)
        if outline.strip():
            break
    if not outline.strip():
        outline = (ckpt.get("formatter_output") or ckpt.get("architect_output") or "").strip()

    bib = _bibliography_stats(root)
    rows = _section_rows(root)
    word_targets = ckpt.get("word_targets") or {}
    level = ckpt.get("academic_level", "unknown")
    style = ckpt.get("citation_style", "apa")
    lang = ckpt.get("language", "en")
    completed = ckpt.get("completed_phase", "none")

    lines: List[str] = []
    lines.append(f"# Paper Map — {topic}")
    lines.append("")
    lines.append(f"- Level: {level} · Citation style: {style} · Language: {lang}")
    lines.append(f"- Pipeline progress: {completed}")
    lines.append(f"- Citations in database: {bib['count']}"
                 + (f" (years {bib['years']})" if bib['years'] else ""))
    lines.append("")

    lines.append("## Outline")
    lines.append("")
    if outline:
        lines.append("```")
        lines.append(_first_meaningful_lines(outline))
        lines.append("```")
    else:
        lines.append("(no outline yet)")
    lines.append("")

    lines.append("## Sections")
    lines.append("")
    lines.append("| section | file | target words | words | status |")
    lines.append("|---|---|---|---|---|")
    for row in rows:
        target = str(word_targets.get(row["section"], "?"))
        lines.append(f"| {row['section']} | {row['file']} | {target} | {row['words']} | {row['status']} |")
    lines.append("")

    lines.append("## Writing discipline")
    lines.append("")
    lines.append("- Cite ONLY `cite_XXX` ids present in `research/bibliography.json`. "
                 "Need a new source? Call `search_literature` first and cite the ids it returns. "
                 "Never invent citations.")
    lines.append("- `write_section` enforces this: unknown cite ids, TODO/[INSERT]/[expand] "
                 "placeholders, or far-too-short sections are rejected — fix and rewrite.")
    lines.append("- After each `write_section`, run `score_draft` (scope=section), read the "
                 "issues, and fix them (revise_section for wording, search_literature for thin "
                 "evidence) before moving on.")
    lines.append("- Key factual claims should be checked with `verify_claims`; apply fixes "
                 "via `revise_section` find_replace.")
    lines.append("- Ground every paragraph in the research material: read the relevant "
                 "`research/papers/*.md` notes before writing, don't write from memory.")
    lines.append("")

    lines.append("## Material")
    lines.append("")
    lines.append("- Research notes: `research/papers/*.md`, `research/combined_research.md`, "
                 "`research/research_gaps.md`")
    lines.append(f"- Citation ledger: `{BIBLIOGRAPHY_REL}` + `drafts/citation_summary.md` "
                 f"({bib['count']} entries)")
    lines.append("- Gaps & trends: `research/research_gaps.md`")
    lines.append("")

    return "\n".join(lines) + "\n"


def write_paper_map(root: Path) -> Path:
    """Generate and write <root>/AGENTS.md. Returns the path."""
    root = Path(root)
    content = generate_paper_map(root)
    out = root / "AGENTS.md"
    out.write_text(content, encoding="utf-8")
    return out
