#!/usr/bin/env python3
"""
ABOUTME: T6 revise_section — targeted revision of one existing section. Applies exact
ABOUTME: find/replace pairs when they match uniquely (no LLM), otherwise falls back to
ABOUTME: LLM revision (call_gemini_revise) with the pairs folded into the instructions.
ABOUTME: Reuses write_section's guardrails, snapshots before overwrite, syncs checkpoint.json.
"""

import contextlib
import io
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agent_tools import registry
from agent_tools.common import (
    SECTION_FILES,
    snapshot_existing,
    sync_checkpoint_section,
    update_section_status,
)
from agent_tools.envelope import ok, fail
from agent_tools.write_section import CITE_REF_RE, _check_guardrails

with contextlib.redirect_stdout(io.StringIO()):
    # utils.revise imports utils.export_professional → utils.pdf_engines → weasyprint,
    # which prints a noisy "external libraries" banner to stdout at import time.
    # The CLI contract is a single JSON envelope line on stdout — silence the banner.
    from utils.revise import call_gemini_revise


@contextlib.contextmanager
def _quiet_stdout():
    """Keep stdout JSON-envelope-clean: redirect prints and detach stdout log handlers."""
    root_logger = logging.getLogger()
    stdout_handlers = [
        h for h in list(root_logger.handlers)
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
    ]
    for h in stdout_handlers:
        root_logger.removeHandler(h)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        for h in stdout_handlers:
            root_logger.addHandler(h)

REVISE_MODEL = "gemini-3-flash-preview"

# Transient error patterns mirrored from utils/revise.py's retry classification
TRANSIENT_PATTERNS = ("rate limit", "429", "timeout", "connection", "overloaded", "capacity")

DESCRIPTION = (
    "Revise one already-written section. Two modes: (1) pass find_replace pairs "
    "[{find, replace}] for deterministic, unique-match edits — consumed directly from "
    "verify_claims' wrong_part/correct_value output — applied without an LLM call when "
    "every pair matches exactly once; (2) pass free-text instructions for an LLM revision. "
    "Ambiguous (2+ matches) or missing pairs are reported as missed_replacements and folded "
    "into the LLM instructions instead. The result must pass the same guardrails as "
    "write_section (word floor, no placeholders, citations must exist in bibliography.json). "
    "Snapshot before overwrite. Requires GOOGLE_API_KEY for the LLM path."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "section": {
            "type": "string",
            "enum": list(SECTION_FILES.keys()),
            "description": "Which section to revise (must exist — write it with "
                           "write_section first).",
        },
        "instructions": {
            "type": "string",
            "minLength": 1,
            "description": "Revision instructions for the LLM (used whenever find_replace "
                           "is absent or has misses).",
        },
        "find_replace": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "find": {"type": "string", "minLength": 1},
                    "replace": {"type": "string"},
                },
                "required": ["find", "replace"],
            },
            "description": "Optional exact-match replacements. A pair is applied only if its "
                           "'find' occurs exactly once; otherwise it lands in "
                           "missed_replacements and is passed to the LLM.",
        },
    },
    "required": ["section", "instructions"],
}


def _normalize_pairs(raw) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """Validate find_replace input. Returns (pairs, error_message)."""
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return None, "find_replace must be an array of {find, replace} objects"
    pairs = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return None, f"find_replace[{i}] must be an object"
        find = item.get("find")
        replace = item.get("replace")
        if not isinstance(find, str) or not find:
            return None, f"find_replace[{i}].find must be a non-empty string"
        if not isinstance(replace, str):
            return None, f"find_replace[{i}].replace must be a string"
        pairs.append({"find": find, "replace": replace})
    return pairs, None


def _pairs_instruction_block(pairs: List[Dict]) -> str:
    lines = [
        "",
        "",
        "Additionally, apply these exact find/replace corrections to the draft "
        "(each 'find' text appears exactly once):",
        "",
    ]
    for i, pair in enumerate(pairs, 1):
        lines.append(f'{i}. Find: "{pair["find"]}"')
        lines.append(f'   Replace with: "{pair["replace"]}"')
    return "\n".join(lines)


def _llm_revise(text: str, instructions: str) -> Dict:
    """Call the revision LLM; returns ok({text}) or fail()."""
    try:
        with _quiet_stdout():
            revised = call_gemini_revise(text, instructions, model=REVISE_MODEL, max_retries=3)
    except Exception as e:
        msg = str(e).lower()
        is_retryable = any(p in msg for p in TRANSIENT_PATTERNS)
        return fail(f"LLM revision failed: {type(e).__name__}: {e}", is_retryable=is_retryable)
    if not revised or not revised.strip():
        return fail("LLM returned an empty revision", is_retryable=True)
    return ok({"text": revised})


def run(args: Dict, root: Path) -> Dict:
    section = args.get("section")
    if section not in SECTION_FILES:
        return fail(f"unknown section: {section} (valid: {sorted(SECTION_FILES)})")

    instructions = args.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        return fail("instructions must be a non-empty string")

    pairs, error = _normalize_pairs(args.get("find_replace"))
    if error:
        return fail(error)

    rel_path = SECTION_FILES[section]["file"]
    target = Path(root) / rel_path
    if not target.exists():
        return fail(
            f"section '{section}' has no file yet ({rel_path} not found) — "
            f"write it first with write_section",
            is_retryable=False,
        )
    current = target.read_text(encoding="utf-8")

    missed: List[Dict] = []
    applied_direct = False
    llm_revised = False

    if pairs:
        for pair in pairs:
            occurrences = current.count(pair["find"])
            if occurrences == 0:
                missed.append({"find": pair["find"], "reason": "not_found"})
            elif occurrences > 1:
                missed.append({
                    "find": pair["find"],
                    "reason": f"ambiguous ({occurrences} matches)",
                })
        if not missed:
            new_text = current
            for pair in pairs:
                new_text = new_text.replace(pair["find"], pair["replace"], 1)
            applied_direct = True
        else:
            # LLM sees the ORIGINAL text — re-state all pairs, not just the missed ones
            revised = _llm_revise(current, instructions.strip() + _pairs_instruction_block(pairs))
            if not revised.get("ok"):
                return revised
            new_text = revised["data"]["text"]
            llm_revised = True
    else:
        revised = _llm_revise(current, instructions.strip())
        if not revised.get("ok"):
            return revised
        new_text = revised["data"]["text"]
        llm_revised = True

    rejected = _check_guardrails(new_text, root, section)
    if rejected:
        return rejected

    snapshot = snapshot_existing(root, rel_path)
    target.write_text(new_text, encoding="utf-8")
    sync = sync_checkpoint_section(root, section, new_text)

    refs = sorted({f"cite_{n}" for n in CITE_REF_RE.findall(new_text)})
    # A revision invalidates the section's last score: resume must re-verify before
    # skipping, and the paper-level finish gate re-checks the word floor anyway.
    status_ledger = update_section_status(
        root, section,
        status="revised",
        words=len(new_text.split()),
        citations_count=len(refs),
        passed=False,
        updated_at=datetime.now().isoformat(timespec="seconds"),
    )

    return ok({
        "section": section,
        "path": rel_path,
        "words": len(new_text.split()),
        "applied_find_replace": applied_direct,
        "missed_replacements": missed,
        "llm_revised": llm_revised,
        "snapshot": snapshot,
        "status_ledger": status_ledger,
        **sync,
    })


registry.register(registry.ToolSpec(
    name="revise_section",
    description=DESCRIPTION,
    input_schema=INPUT_SCHEMA,
    func=run,
))
