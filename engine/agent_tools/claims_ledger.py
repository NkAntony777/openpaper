#!/usr/bin/env python3
"""
ABOUTME: T9 manage_claims — the paper's CLAIM LEDGER. record/list/verify/resolve over
ABOUTME: drafts/.ledger/<section>.claims.jsonl. CONTRADICTED verdicts emit find_replace
ABOUTME: (T4→T6); resolve is evidence-checked. Offline-first: record/list/resolve never
ABOUTME: need a key. Unresolved CONTRADICTED entries fail the paper finish gate.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from agent_tools import registry
from agent_tools.common import SECTION_FILES
from agent_tools.envelope import ok, fail
from agent_tools.claims import _quiet_stdout, verdicts_to_find_replace

LEDGER_DIR_REL = "drafts/.ledger"
ID_PREFIX = "CL"
NO_KEY_MESSAGE = (
    "verification needs GOOGLE_API_KEY; recording and listing work offline"
)

RESOLVE_STATUSES = ("revised", "deleted")

DESCRIPTION = (
    "Manage the paper's claim ledger (drafts/.ledger/<section>.claims.jsonl) — the "
    "persistent, per-section record of key factual claims. action='record' appends claims "
    "with stable ids (CL-<SECTION>-<N>) for later audit; action='list' reads them back "
    "(offline, no key needed); action='verify' fact-checks the section's recorded claims "
    "with web-grounded evidence and writes each verdict back into the ledger (requires "
    "GOOGLE_API_KEY), returning a find_replace list for CONTRADICTED claims; "
    "action='resolve' marks a CONTRADICTED claim as revised or deleted after the draft "
    "change is on disk (evidence-checked: deleted claims must no longer appear; revised "
    "claims must have their wrong_part gone). Unresolved CONTRADICTED entries fail the "
    "paper finish gate. Use record after writing hard factual/quantitative claims, list "
    "to audit, verify before compile_draft, and resolve after every CONTRADICTED fix. "
    "Do NOT use it for style/completeness judgment (score_draft) or ad-hoc checks you do "
    "not intend to persist (verify_claims)."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["record", "list", "verify", "resolve"],
            "description": "record: append claims to the section's ledger. list: read the "
                           "ledger back (offline). verify: fact-check recorded claims and "
                           "persist verdicts (needs GOOGLE_API_KEY). resolve: mark a "
                           "CONTRADICTED claim revised|deleted after the draft change is on disk.",
        },
        "section": {
            "type": "string",
            "enum": list(SECTION_FILES.keys()),
            "description": "Which section's ledger to operate on.",
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "minLength": 1},
                    "line": {"type": "string"},
                    "id": {"type": "string"},
                    "status": {"type": "string", "enum": list(RESOLVE_STATUSES)},
                    "note": {"type": "string"},
                },
            },
            "description": "For action='record': claims to append ({claim, line?}). "
                           "For action='verify': optional subset to verify (default: all "
                           "recorded claims without a verdict yet). "
                           "For action='resolve': [{id|claim, status=revised|deleted, note?}].",
        },
        "max_workers": {
            "type": "integer",
            "default": 10,
            "description": "Max parallel verification threads for action='verify' (default 10).",
        },
    },
    "required": ["action", "section"],
}


def _ledger_path(root: Path, section: str) -> Path:
    return Path(root) / LEDGER_DIR_REL / f"{section}.claims.jsonl"


def _read_entries(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    entries: List[Dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def _write_entries(path: Path, entries: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
    path.write_text(text, encoding="utf-8")


def _next_id_number(entries: List[Dict], section: str) -> int:
    highest = 0
    prefix = f"{ID_PREFIX}-{section.upper()}-"
    for e in entries:
        eid = str(e.get("id") or "")
        if eid.startswith(prefix):
            m = re.match(re.escape(prefix) + r"(\d+)$", eid)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1


def _entry_verdict(entry: Dict) -> str:
    v = entry.get("verdict")
    if isinstance(v, dict):
        return str(v.get("verdict") or "")
    return str(v or "")


def _is_resolved(entry: Dict) -> bool:
    res = entry.get("resolution")
    if not isinstance(res, dict):
        return False
    return res.get("status") in RESOLVE_STATUSES


def iter_ledger_entries(root) -> List[Dict]:
    """All claims-ledger entries across sections, each tagged with 'section'."""
    root = Path(root)
    d = root / LEDGER_DIR_REL
    out: List[Dict] = []
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.claims.jsonl")):
        section = path.name[: -len(".claims.jsonl")]
        for e in _read_entries(path):
            item = dict(e)
            item.setdefault("section", section)
            out.append(item)
    return out


def unresolved_contradictions(root) -> List[Dict]:
    """CONTRADICTED ledger entries that have not been revised or deleted."""
    return [
        e for e in iter_ledger_entries(root)
        if _entry_verdict(e) == "CONTRADICTED" and not _is_resolved(e)
    ]


def _record(args: Dict, root: Path, section: str) -> Dict:
    claims = args.get("claims")
    if not isinstance(claims, list) or not claims:
        return fail("claims must be a non-empty array of {claim, line?} objects")

    normalized: List[Dict] = []
    for i, item in enumerate(claims):
        if not isinstance(item, dict):
            return fail(f"claims[{i}] must be an object with a 'claim' field")
        text = item.get("claim")
        if not isinstance(text, str) or not text.strip():
            return fail(f"claims[{i}] is missing a non-empty 'claim' field")
        normalized.append({"claim": text.strip(), "line": item.get("line", "")})

    path = _ledger_path(root, section)
    entries = _read_entries(path)
    n = _next_id_number(entries, section)
    recorded_ids: List[str] = []
    now = datetime.now().isoformat(timespec="seconds")
    for offset, item in enumerate(normalized):
        eid = f"{ID_PREFIX}-{section.upper()}-{n + offset}"
        entries.append({
            "id": eid,
            "claim": item["claim"],
            "line": item.get("line", ""),
            "recorded_at": now,
        })
        recorded_ids.append(eid)
    _write_entries(path, entries)

    return ok({
        "recorded": len(normalized),
        "path": f"{LEDGER_DIR_REL}/{section}.claims.jsonl",
        "ids": recorded_ids,
    })


def _list(args: Dict, root: Path, section: str) -> Dict:
    path = _ledger_path(root, section)
    entries = _read_entries(path)
    return ok({
        "section": section,
        "path": f"{LEDGER_DIR_REL}/{section}.claims.jsonl",
        "count": len(entries),
        "claims": entries,
    })


def _verify(args: Dict, root: Path, section: str) -> Dict:
    path = _ledger_path(root, section)
    entries = _read_entries(path)
    if not entries:
        return fail(f"no recorded claims for section '{section}' — action='record' first")

    subset = args.get("claims")
    if isinstance(subset, list) and subset:
        wanted = set()
        for item in subset:
            if isinstance(item, dict) and isinstance(item.get("claim"), str):
                wanted.add(item["claim"].strip())
            elif isinstance(item, str):
                wanted.add(item.strip())
        pending = [e for e in entries if e.get("claim") in wanted]
        if not pending:
            return fail("none of the requested claims match the recorded ledger")
    else:
        pending = [e for e in entries if not e.get("verdict")]

    if not pending:
        stored = []
        for e in entries:
            v = e.get("verdict")
            if isinstance(v, dict):
                item = dict(v)
                item.setdefault("claim", e.get("claim"))
                stored.append(item)
        return ok({
            "section": section,
            "verdicts": stored,
            "count": 0,
            "message": "all recorded claims already carry a verdict",
            "find_replace": verdicts_to_find_replace(stored),
            "unresolved_contradicted": len(unresolved_contradictions(root)),
        })

    try:
        max_workers = int(args.get("max_workers") or 10)
    except (TypeError, ValueError):
        return fail("max_workers must be an integer")
    max_workers = max(1, min(max_workers, 20))

    from config import get_config
    config = get_config()
    api_key = config.google_api_key
    if not api_key:
        return fail(NO_KEY_MESSAGE, is_retryable=False, missing_env="GOOGLE_API_KEY")

    try:
        from utils.agent_runner import setup_model
        model = setup_model()
    except Exception as e:
        return fail(f"could not set up judge model: {type(e).__name__}: {e}", is_retryable=False)

    claims_in = [{"claim": e["claim"], "section": section, "line": str(e.get("line") or "")}
                 for e in pending]
    try:
        from utils.factcheck_verifier import FactCheckVerifier
        with _quiet_stdout():
            verifier = FactCheckVerifier(api_key=api_key, model=model)
            verdicts = verifier.verify_claims(claims_in, max_workers=max_workers)
    except Exception as e:
        return fail(f"fact-check failed: {type(e).__name__}: {e}", is_retryable=True)

    by_claim = {v.get("claim"): v for v in verdicts if isinstance(v, dict)}
    for e in pending:
        v = by_claim.get(e.get("claim"))
        if v is not None:
            e["verdict"] = {
                "verdict": v.get("verdict"),
                "confidence": v.get("confidence"),
                "evidence_snippet": v.get("evidence_snippet"),
                "wrong_part": v.get("wrong_part"),
                "correct_value": v.get("correct_value"),
                "source_url": v.get("source_url"),
                "verified_at": datetime.now().isoformat(timespec="seconds"),
            }
    _write_entries(path, entries)

    return ok({
        "section": section,
        "verdicts": verdicts,
        "count": len(verdicts),
        "contradicted": sum(1 for v in verdicts if v.get("verdict") == "CONTRADICTED"),
        "find_replace": verdicts_to_find_replace(verdicts),
        "path": f"{LEDGER_DIR_REL}/{section}.claims.jsonl",
        "unresolved_contradicted": len(unresolved_contradictions(root)),
    })


def _find_entry(entries: List[Dict], item: Dict) -> Optional[Dict]:
    eid = item.get("id")
    if isinstance(eid, str) and eid.strip():
        wanted = eid.strip()
        for e in entries:
            if e.get("id") == wanted:
                return e
        return None
    claim = item.get("claim")
    if isinstance(claim, str) and claim.strip():
        wanted = claim.strip()
        for e in entries:
            if e.get("claim") == wanted:
                return e
    return None


def _resolve(args: Dict, root: Path, section: str) -> Dict:
    items = args.get("claims")
    if not isinstance(items, list) or not items:
        return fail("claims must be a non-empty array of {id|claim, status, note?} objects")

    path = _ledger_path(root, section)
    entries = _read_entries(path)
    if not entries:
        return fail(f"no recorded claims for section '{section}' — action='record' first")

    rel = SECTION_FILES[section]["file"]
    section_file = Path(root) / rel
    text = section_file.read_text(encoding="utf-8") if section_file.exists() else ""
    text_l = text.lower()

    now = datetime.now().isoformat(timespec="seconds")
    resolved_ids: List[str] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            return fail(f"claims[{i}] must be an object with id|claim and status")
        status = item.get("status")
        if status not in RESOLVE_STATUSES:
            return fail(
                f"claims[{i}].status must be one of {list(RESOLVE_STATUSES)} (got {status!r})"
            )
        entry = _find_entry(entries, item)
        if entry is None:
            return fail(f"claims[{i}] does not match any recorded ledger entry")

        claim_text = str(entry.get("claim") or "")
        wrong = ""
        v = entry.get("verdict")
        if isinstance(v, dict):
            wrong = str(v.get("wrong_part") or "")

        if status == "deleted":
            if claim_text and claim_text.lower() in text_l:
                return fail(
                    f"{entry.get('id')}: status=deleted but the claim text is still in "
                    f"{rel} — remove or rewrite it, then resolve",
                    is_retryable=True,
                )
        else:  # revised
            needle = wrong.strip() or claim_text
            if needle and needle.lower() in text_l:
                kind = "wrong_part" if wrong.strip() else "claim text"
                return fail(
                    f"{entry.get('id')}: status=revised but the {kind} is still in "
                    f"{rel} — apply revise_section find_replace first",
                    is_retryable=True,
                )

        note = item.get("note") if isinstance(item.get("note"), str) else ""
        entry["resolution"] = {
            "status": status,
            "note": note,
            "resolved_at": now,
        }
        resolved_ids.append(entry.get("id") or "")

    _write_entries(path, entries)
    return ok({
        "section": section,
        "resolved": len(resolved_ids),
        "ids": resolved_ids,
        "path": f"{LEDGER_DIR_REL}/{section}.claims.jsonl",
        "unresolved_contradicted": len(unresolved_contradictions(root)),
    })


def run(args: Dict, root: Path) -> Dict:
    action = args.get("action")
    if action not in ("record", "list", "verify", "resolve"):
        return fail(f"action must be one of record|list|verify|resolve (got {action!r})")
    section = args.get("section")
    if section not in SECTION_FILES:
        return fail(f"unknown section: {section} (valid: {sorted(SECTION_FILES)})")

    if action == "record":
        return _record(args, root, section)
    if action == "list":
        return _list(args, root, section)
    if action == "verify":
        return _verify(args, root, section)
    return _resolve(args, root, section)


registry.register(registry.ToolSpec(
    name="manage_claims",
    description=DESCRIPTION,
    input_schema=INPUT_SCHEMA,
    func=run,
))
