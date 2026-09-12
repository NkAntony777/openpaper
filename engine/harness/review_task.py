#!/usr/bin/env python3
"""
ABOUTME: Global review-task prompt builder (M2 non-linear oversight).
ABOUTME: Reads the status ledger (section_status.json) + the section-summary ledger
ABOUTME: (drafts/.ledger/*.md) and asks the agent for a cross-section consistency review
ABOUTME: with a strict markdown issue format the CLI can persist to global_issues.md.
"""

import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_tools.common import SECTION_FILES, read_section_status

STATUS_REL = "section_status.json"
LEDGER_DIR_REL = "drafts/.ledger"
GLOBAL_ISSUES_HEADING = "# Global Issues"
NO_ISSUES_TEXT = "# Global Issues\n\nNo cross-section issues found."


def _ledger_summaries(root: Path) -> List[str]:
    d = root / LEDGER_DIR_REL
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.md") if p.is_file())


def _status_digest(status: dict) -> List[str]:
    """Compact one-liner per ledger entry, for the prompt's status digest block."""
    lines = []
    for name in SECTION_FILES:
        entry = (status.get("sections") or {}).get(name)
        if not isinstance(entry, dict):
            continue
        state = entry.get("status", "?")
        passed = entry.get("passed")
        mark = {True: "pass", False: "FAIL"}.get(passed, "unscored")
        n_issues = len(entry.get("open_issues") or [])
        lines.append(f"- {name}: {state}, score {mark}, {n_issues} open issue(s)")
    full = status.get("full")
    if isinstance(full, dict) and "last_total" in full:
        verdict = "pass" if full.get("last_passed") else "fail"
        lines.append(f"- full draft: {full.get('last_total')}/100 ({verdict})")
    return lines


def build_review_prompt(root) -> str:
    """Build the global cross-section review prompt. Pure read, no side effects."""
    root = Path(root)
    status = read_section_status(root)
    summaries = _ledger_summaries(root)

    material = [
        "- `AGENTS.md` (the paper map: outline digest, per-section status + open issues, "
        "section summaries, bibliography stats) — read it first.",
        f"- `{STATUS_REL}` — the machine-readable status ledger.",
    ]
    if summaries:
        material.append("- Section summaries (short — read each one):")
        for name in summaries:
            material.append(f"  - `{LEDGER_DIR_REL}/{name}`")
    else:
        material.append(
            "- No section summaries found in `drafts/.ledger/` yet — read the section drafts "
            "themselves; they are the review input."
        )

    digest = _status_digest(status)
    digest_block = "\n".join(digest) if digest else "(status ledger is empty or missing)"

    lines = [
        "Perform a GLOBAL cross-section review of the academic paper in this directory.",
        "",
        "Material (read it with read_artifact — never review from memory):",
        *material,
        "",
        "Current status digest (from the ledger):",
        digest_block,
        "",
        "Review dimensions — check every one across ALL sections:",
        "1. Terminology & symbol consistency: the same concept or variable must use the same "
        "name everywhere, including math notation.",
        "2. Narrative line: what the introduction promises vs. what each section delivers vs. "
        "how the conclusion echoes it.",
        "3. Cross-section redundancy and contradictions: duplicate passages, conflicting claims "
        "or numbers between sections.",
        "4. Citation consistency: the same claim must be cited the same way in every section; "
        "flag works cited but never used, and claims that should cite a known source but do not.",
        "5. Outline conformance: each section must deliver what the outline assigned it — no "
        "missing promised subsections, no off-scope additions.",
        "",
        "Output format — strict markdown and nothing else. Start with the heading "
        f"`{GLOBAL_ISSUES_HEADING}`, then one block per issue, ordered by severity "
        "(high first):",
        "",
        "## GI-1 [high|medium|low] scope: global|<section name>",
        "Issue: <one sentence>",
        "Suggested fix: <a fix directed at a specific section>",
        "",
        "Number issues GI-1, GI-2, ... If — and only if — there are no cross-section issues, "
        "output exactly:",
        "",
        NO_ISSUES_TEXT,
    ]
    return "\n".join(lines) + "\n"
