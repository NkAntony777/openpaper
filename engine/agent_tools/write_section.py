#!/usr/bin/env python3
"""
ABOUTME: T2 write_section — the core writing primitive. Idempotent full-section write with
ABOUTME: guardrails (word floor, anti-laziness placeholders, citation whitelist), snapshot
ABOUTME: before overwrite, and checkpoint.json sync (including body_output rebuild).
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agent_tools import registry
from agent_tools.common import (
    CUSTOM_SECTIONS_DIR,
    SECTION_FILES,
    bibliography_ids,
    snapshot_existing,
    sync_checkpoint_section,
    update_section_status,
    word_target_max,
)
from agent_tools.envelope import ok, fail, ToolInputError, resolve_under_root

CITE_REF_RE = re.compile(r"\{cite_(\d+)\}")
CITE_MISSING_RE = re.compile(r"\{cite_MISSING[^}]*\}", re.IGNORECASE)

# Section-summary ledger: low-token carriers for the global review pass.
LEDGER_DIR_REL = "drafts/.ledger"
SUMMARY_MAX_CHARS = 600

# Hard anti-laziness placeholders — rejection (NEW_ISSUES_DEC2025 ticket class)
HARD_PLACEHOLDER_PATTERNS = [
    (re.compile(r"\bTODO\b"), "TODO marker"),
    (re.compile(r"\[INSERT[^\]]*\]", re.IGNORECASE), "[INSERT] placeholder"),
    (re.compile(r"\[expand[^\]]*\]", re.IGNORECASE), "[expand] placeholder"),
    (re.compile(r"\bTBD\b"), "TBD marker"),
    (re.compile(r"Lorem ipsum", re.IGNORECASE), "Lorem ipsum filler"),
]

WORD_FLOOR_RATIO = 0.7  # guardrail: at least 70% of the section's word target

DESCRIPTION = (
    "Write (or fully rewrite) one paper section as markdown. Idempotent: same input produces "
    "the same file, so quality-gate revise loops are safe to retry. Guardrails reject lazy or "
    "unsupported output: word floor (~70% of target), TODO/[INSERT]/[expand]/TBD/Lorem "
    "placeholders, and citations not present in research/bibliography.json — always run "
    "search_literature BEFORE citing a source you have not seen in the citation database. "
    "{cite_MISSING:...} placeholders are allowed but reported (compile_draft researches them)."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": list(SECTION_FILES.keys()) + ["custom"],
            "description": "Which section to write.",
        },
        "content": {
            "type": "string",
            "minLength": 1,
            "description": "Full markdown content of the section.",
        },
        "citations_used": {
            "type": "array",
            "items": {"type": "string"},
            "description": "cite_XXX ids cited in this section. Each must exist in "
                           "research/bibliography.json (checked against the file, not just this list).",
        },
        "slug": {
            "type": "string",
            "description": "Required for section='custom': filename slug, e.g. 'related_work'. "
                           "Writes to drafts/custom_sections/custom_<slug>.md.",
        },
        "summary": {
            "type": "string",
            "description": "One or two sentences summarizing this section's core claims, key "
                           "terminology and main cited ids — the global review pass "
                           "(harness review) reads these summaries, so always provide one. "
                           "Max 600 characters (truncated beyond that).",
        },
    },
    "required": ["section", "content"],
}


def _section_target_rel(args: Dict) -> Tuple[str, str]:
    """Return (output rel path, section key) for the requested section."""
    section = args.get("section")
    if section == "custom":
        slug = args.get("slug")
        if not slug or not re.fullmatch(r"[a-z0-9_\-]{2,60}", slug):
            raise ToolInputError(
                "section='custom' requires a slug matching [a-z0-9_-]{2,60}"
            )
        # NN = next free index among existing custom sections
        return f"{CUSTOM_SECTIONS_DIR}/custom_{slug}.md", "custom"
    if section not in SECTION_FILES:
        raise ToolInputError(f"unknown section: {section} (valid: {sorted(SECTION_FILES) + ['custom']})")
    return SECTION_FILES[section]["file"], section


def _check_guardrails(content: str, root: Path, section: str) -> Optional[Dict]:
    """Return a fail() envelope on violation, else None."""
    for pattern, label in HARD_PLACEHOLDER_PATTERNS:
        if pattern.search(content):
            return fail(
                f"guardrail: content contains {label}; finish the section for real",
                is_retryable=False, guardrail="placeholder", placeholder=label,
            )

    bib_ids = bibliography_ids(root)
    refs_in_text = {f"cite_{n}" for n in CITE_REF_RE.findall(content)}
    unknown = sorted(refs_in_text - bib_ids)
    if unknown:
        return fail(
            f"guardrail: citations not in research/bibliography.json: {unknown}. "
            f"Run search_literature first, then cite the returned ids.",
            is_retryable=False, guardrail="unknown_citations", unknown_citations=unknown,
        )

    target = word_target_max(root, SECTION_FILES.get(section, {}).get("wt_key", ""))
    if target > 0:
        words = len(content.split())
        floor = int(target * WORD_FLOOR_RATIO)
        if words < floor:
            return fail(
                f"guardrail: section too short: {words} words (floor: {floor}, target: {target}). "
                f"Expand the section before writing.",
                is_retryable=False, guardrail="min_words",
                actual_words=words, floor_words=floor, target_words=target,
            )
    return None


def _summary_ledger_rel(section: str, slug: Optional[str]) -> str:
    if section == "custom":
        return f"{LEDGER_DIR_REL}/custom_{slug}.md"
    return f"{LEDGER_DIR_REL}/{section}.summary.md"


def run(args: Dict, root: Path) -> Dict:
    content = args.get("content")
    if not isinstance(content, str) or not content.strip():
        return fail("content must be a non-empty string")

    try:
        rel_path, section = _section_target_rel(args)
        target = resolve_under_root(root, rel_path)
    except ToolInputError as e:
        return fail(str(e))

    rejected = _check_guardrails(content, root, section)
    if rejected:
        return rejected

    snapshot = snapshot_existing(root, rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    sync = sync_checkpoint_section(root, section, content) if section != "custom" else {
        "checkpoint": "skipped", "body": False,
    }

    refs = sorted({f"cite_{n}" for n in CITE_REF_RE.findall(content)})
    warnings: List[str] = []
    if CITE_MISSING_RE.search(content):
        warnings.append("content contains {cite_MISSING:...}; compile_draft will try to research real replacements")
    declared_raw = args.get("citations_used")
    if declared_raw is not None:
        declared = {str(c) for c in declared_raw}
        undeclared = sorted(set(refs) - declared)
        if undeclared:
            warnings.append(f"citations_used omitted refs present in text: {undeclared}")

    summary_written = None
    summary_raw = args.get("summary")
    if summary_raw is not None:
        if isinstance(summary_raw, str) and summary_raw.strip():
            summary_text = summary_raw.strip()
            if len(summary_text) > SUMMARY_MAX_CHARS:
                summary_text = summary_text[:SUMMARY_MAX_CHARS]
                warnings.append(f"summary truncated to {SUMMARY_MAX_CHARS} characters")
            summary_written = _summary_ledger_rel(section, args.get("slug") if section == "custom" else None)
            ledger_path = Path(root) / summary_written
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.write_text(summary_text, encoding="utf-8")
        else:
            warnings.append("summary ignored: must be a non-empty string")

    status_ledger = None
    if section != "custom":
        status_ledger = update_section_status(
            root, section,
            status="written",
            words=len(content.split()),
            citations_count=len(refs),
            updated_at=datetime.now().isoformat(timespec="seconds"),
        )

    return ok({
        "section": section,
        "path": rel_path,
        "words": len(content.split()),
        "citations": refs,
        "warnings": warnings,
        "snapshot": snapshot,
        "summary_ledger": summary_written,
        "status_ledger": status_ledger,
        **sync,
    })


registry.register(registry.ToolSpec(
    name="write_section",
    description=DESCRIPTION,
    input_schema=INPUT_SCHEMA,
    func=run,
))
