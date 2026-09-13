#!/usr/bin/env python3
"""
ABOUTME: AGENTS.md paper map generator (design doc §7.1).
ABOUTME: The map is the paper-level "repo map": gives the agent a <=1.5k-token view of the
ABOUTME: whole paper so it can decide where to drill in via read_artifact. When the status
ABOUTME: ledger (section_status.json) and/or the summary ledger (drafts/.ledger/) exist,
ABOUTME: their digests are rendered as bounded blocks (truncation keeps the map cheap).
"""

import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from agent_tools.common import (
    SECTION_FILES,
    SECTION_STATUS_REL,
    read_checkpoint,
    read_section_status,
)

OUTLINE_CANDIDATES = [
    "drafts/00_formatted_outline.md",
    "drafts/00_outline.md",
]

BIBLIOGRAPHY_REL = "research/bibliography.json"
LEDGER_DIR_REL = "drafts/.ledger"
OPEN_ISSUES_MAX = 10
OPEN_ISSUE_MAX_CHARS = 120
SUMMARY_SNIPPET_MAX_CHARS = 160


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
        rows.append(
            {
                "section": name,
                "file": meta["file"],
                "words": _word_count(text),
                "status": "written" if text.strip() else "pending",
            }
        )
    return rows


# ------------------------------------------------------------- ledger digest blocks


def _score_cells(entry: Optional[Dict]):
    """(score, issues) display cells for the Sections table, from a ledger entry."""
    if not isinstance(entry, dict):
        return "—", "—"
    passed = entry.get("passed")
    score = {True: "✓", False: "✗"}.get(passed, "—")
    issues = entry.get("open_issues")
    issues_n = str(len(issues)) if isinstance(issues, list) else "—"
    return score, issues_n


def _open_issues_block(status: Dict) -> List[str]:
    lines = ["## Open issues", ""]
    full = status.get("full")
    if isinstance(full, dict) and "last_total" in full:
        verdict = "pass" if full.get("last_passed") else "fail"
        lines.append(f"Full draft: {full.get('last_total')}/100 ({verdict})")
        lines.append("")
    emitted = 0
    for name in SECTION_FILES:
        entry = (status.get("sections") or {}).get(name)
        if not isinstance(entry, dict):
            continue
        for issue in entry.get("open_issues") or []:
            if emitted >= OPEN_ISSUES_MAX:
                lines.append(f"- … (further issues in {SECTION_STATUS_REL})")
                return lines
            lines.append(f"- {name}: {str(issue)[:OPEN_ISSUE_MAX_CHARS]}")
            emitted += 1
    if emitted == 0:
        lines.append("(none)")
    return lines


def _summaries_block(root: Path) -> List[str]:
    lines = ["## Section summaries", ""]
    d = root / LEDGER_DIR_REL
    files = sorted(p for p in d.glob("*.md") if p.is_file()) if d.is_dir() else []
    if not files:
        lines.append("(none yet — write_section's `summary` argument fills this)")
        return lines
    for p in files:
        name = p.name[: -len(".summary.md")] if p.name.endswith(".summary.md") else p.stem
        first_line = next(
            (ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()), ""
        )
        lines.append(f"- {name}: {first_line[:SUMMARY_SNIPPET_MAX_CHARS]}")
    return lines


LESSONS_APPROVED_REL = "lessons/approved"
LESSON_SNIPPET_MAX_CHARS = 300
REPO_TEMPLATES_LESSONS = Path(__file__).resolve().parents[2] / "templates" / "lessons"


def seed_approved_lessons(root: Path, templates_dir: Optional[Path] = None) -> int:
    """Copy templates/lessons/*.md into <root>/lessons/approved/ if missing.

    Cross-run memory: humans promote distilled lessons into templates/lessons/;
    the next paper run picks them up here. Existing approved files are never overwritten.
    Returns the number of files copied.
    """
    src_dir = Path(templates_dir) if templates_dir is not None else REPO_TEMPLATES_LESSONS
    if not src_dir.is_dir():
        return 0
    files = [p for p in sorted(src_dir.glob("*.md")) if p.is_file()]
    if not files:
        return 0
    dest = Path(root) / LESSONS_APPROVED_REL
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in files:
        target = dest / src.name
        if target.exists():
            continue
        shutil.copyfile(src, target)
        n += 1
    return n


def _lessons_block(root: Path) -> List[str]:
    """Approved distilled lessons (engine/harness/journal_distill.py writes proposals to
    lessons_proposed.md; humans promote them into lessons/approved/<name>.md)."""
    lines = ["## Lessons learned", ""]
    d = root / LESSONS_APPROVED_REL
    files = sorted(p for p in d.glob("*.md") if p.is_file()) if d.is_dir() else []
    if not files:
        lines.append("(none yet — approved lessons from `opendraft harness distill` appear here)")
        return lines
    for p in files:
        text = " ".join(p.read_text(encoding="utf-8").split())
        lines.append(f"- {p.stem}: {text[:LESSON_SNIPPET_MAX_CHARS]}")
    return lines


def _forbidden_from_ckpt(ckpt: Dict) -> List[str]:
    brief = ckpt.get("research_brief") or {}
    if not isinstance(brief, dict):
        return []
    raw = brief.get("forbidden_claims") or brief.get("negative_claims") or []
    if not isinstance(raw, list):
        return []
    return [str(c).strip() for c in raw if str(c).strip()]


def _claims_digest_block(root: Path) -> List[str]:
    """Open CONTRADICTED claims from the jsonl ledger. Omitted entirely when unused."""
    d = root / LEDGER_DIR_REL
    files = sorted(p for p in d.glob("*.claims.jsonl") if p.is_file()) if d.is_dir() else []
    if not files:
        return []
    lines = ["## Claims ledger", ""]
    open_n = 0
    total = 0
    for p in files:
        for raw in p.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            total += 1
            v = item.get("verdict")
            verdict = v.get("verdict") if isinstance(v, dict) else v
            res = item.get("resolution") if isinstance(item.get("resolution"), dict) else {}
            if verdict == "CONTRADICTED" and res.get("status") not in ("revised", "deleted"):
                open_n += 1
                claim = str(item.get("claim") or "")[:OPEN_ISSUE_MAX_CHARS]
                lines.append(f"- OPEN {item.get('id')}: {claim}")
    if open_n == 0:
        lines.append(f"(all clean — {total} recorded claim(s), 0 unhandled CONTRADICTED)")
    return lines


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
    lines.append(
        f"- Citations in database: {bib['count']}"
        + (f" (years {bib['years']})" if bib["years"] else "")
    )
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
    has_ledger = (root / SECTION_STATUS_REL).exists()
    if has_ledger:
        status = read_section_status(root)
        lines.append("| section | file | target words | words | status | score | issues |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in rows:
            target = str(word_targets.get(row["section"], "?"))
            score, issues_n = _score_cells((status.get("sections") or {}).get(row["section"]))
            lines.append(
                f"| {row['section']} | {row['file']} | {target} | {row['words']} | "
                f"{row['status']} | {score} | {issues_n} |"
            )
        lines.append("")
        lines.extend(_open_issues_block(status))
        lines.append("")
        lines.extend(_summaries_block(root))
    else:
        lines.append("| section | file | target words | words | status |")
        lines.append("|---|---|---|---|---|")
        for row in rows:
            target = str(word_targets.get(row["section"], "?"))
            lines.append(
                f"| {row['section']} | {row['file']} | {target} | {row['words']} | {row['status']} |"
            )
    lines.append("")
    lines.extend(_lessons_block(root))
    lines.append("")
    claims_block = _claims_digest_block(root)
    if claims_block:
        lines.extend(claims_block)
        lines.append("")

    lines.append("## Writing discipline")
    lines.append("")
    lines.append(
        "- Cite ONLY `cite_XXX` ids present in `research/bibliography.json`. "
        "Need a new source? Call `search_literature` first and cite the ids it returns. "
        "Never invent citations."
    )
    lines.append(
        "- `write_section` enforces this: unknown cite ids, TODO/[INSERT]/[expand] "
        "placeholders, or far-too-short sections are rejected — fix and rewrite."
    )
    lines.append(
        "- After each `write_section`, run `score_draft` (scope=section), read the "
        "issues, and fix them (revise_section for wording, search_literature for thin "
        "evidence) before moving on."
    )
    lines.append(
        "- Record hard factual claims with `manage_claims` (action=record), verify "
        "them, and feed CONTRADICTED `find_replace` pairs into `revise_section`. Then "
        "`manage_claims` action=resolve so the finish gate can see they were handled."
    )
    lines.append(
        "- Ground every paragraph in the research material: read the relevant "
        "`research/papers/*.md` notes before writing, don't write from memory."
    )
    forbidden = _forbidden_from_ckpt(ckpt)
    if forbidden:
        lines.append(
            "- FORBIDDEN claims (must NOT appear anywhere; the finish gate scans for them):"
        )
        for c in forbidden:
            lines.append(f"  - {c}")
    lines.append("")

    lines.append("## Material")
    lines.append("")
    lines.append(
        "- Research notes: `research/papers/*.md`, `research/combined_research.md`, "
        "`research/research_gaps.md`"
    )
    lines.append(
        f"- Citation ledger: `{BIBLIOGRAPHY_REL}` + `drafts/citation_summary.md` "
        f"({bib['count']} entries)"
    )
    lines.append(
        "- Claims ledger: `drafts/.ledger/*.claims.jsonl` — key factual claims per "
        "section, recorded/verified via `manage_claims`"
    )
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
