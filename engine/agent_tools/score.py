#!/usr/bin/env python3
"""
ABOUTME: T5 score_draft — quality gate primitive. Read-only, idempotent, side-effect free.
ABOUTME: scope=full runs the same 100-point gate as the pipeline (structured issues);
ABOUTME: scope=section gives a per-section check (word floor + placeholders + citations).
"""

import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict

from agent_tools import registry
from agent_tools.common import SECTION_FILES, read_checkpoint, word_target_max
from agent_tools.envelope import ok, fail, ToolInputError, resolve_under_root
from utils.quality_gate import STRUCTURE_ERROR_PATTERNS, score_texts

CITE_REF_RE = re.compile(r"\{cite_(\d+)\}")
CITE_MISSING_RE = re.compile(r"\{cite_MISSING[^}]*\}", re.IGNORECASE)

DESCRIPTION = (
    "Score draft quality — the feedback signal for revise loops. Read-only: call it after "
    "every write_section, read the structured issues, and decide the fix yourself "
    "(revise_section for wording, search_literature for thin evidence). "
    "scope='section' checks one section (word floor ~70% of target, no lazy placeholders, "
    "citation count); scope='full' scores the whole draft on the pipeline's 100-point gate "
    "(word count / citations / completeness / structure), pass mark 50."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "scope": {"type": "string", "enum": ["section", "full"], "default": "section"},
        "section": {
            "type": "string",
            "enum": list(SECTION_FILES.keys()),
            "description": "Required when scope='section'.",
        },
    },
}


def _checkpoint_hints(root: Path) -> Dict:
    ckpt = read_checkpoint(root) or {}
    return {
        "academic_level": ckpt.get("academic_level", "master"),
        "min_citations": (ckpt.get("word_targets") or {}).get("min_citations", 10),
    }


def _read_section_texts(root: Path) -> Dict[str, str]:
    texts: Dict[str, str] = {}
    for name, meta in SECTION_FILES.items():
        f = Path(root) / meta["file"]
        texts[name] = f.read_text(encoding="utf-8") if f.exists() else ""
    return texts


def _score_full(root: Path) -> Dict:
    hints = _checkpoint_hints(root)
    texts = _read_section_texts(root)
    body = "\n\n".join(
        t for t in (texts["literature_review"], texts["methodology"], texts["results"], texts["discussion"]) if t
    )
    q = score_texts(
        texts={
            "introduction": texts["introduction"],
            "body": body,
            "conclusion": texts["conclusion"],
            "literature_review": texts["literature_review"],
            "methodology": texts["methodology"],
            "results": texts["results"],
        },
        academic_level=hints["academic_level"],
        min_citations=hints["min_citations"],
    )
    return ok({
        "scope": "full",
        "total": q.total_score,
        "passed": q.passed,
        "breakdown": {
            "word_count": q.word_count_score,
            "citations": q.citation_score,
            "completeness": q.completeness_score,
            "structure": q.structure_score,
        },
        "issues": [asdict(i) for i in q.structured_issues],
    })


def _score_section(root: Path, section: str) -> Dict:
    meta = SECTION_FILES.get(section)
    if meta is None:
        raise ToolInputError(f"unknown section: {section} (valid: {sorted(SECTION_FILES)})")
    f = Path(root) / meta["file"]
    content = f.read_text(encoding="utf-8") if f.exists() else ""

    words = len(content.split())
    target = word_target_max(root, meta["wt_key"])
    floor = int(target * 0.7) if target > 0 else 0
    refs = sorted({f"cite_{n}" for n in CITE_REF_RE.findall(content)})

    issues = []
    if not content.strip():
        issues.append({"metric": "present", "severity": "error", "message": "section file is empty or missing"})
    if floor and words < floor:
        issues.append({"metric": "word_count", "severity": "warning",
                       "actual": words, "target": target,
                       "message": f"section short: {words} words (floor {floor}, target {target})"})
    for pattern, _metric, message in STRUCTURE_ERROR_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            issues.append({"metric": "placeholder", "severity": "error", "message": message})

    hard_failed = any(i["severity"] == "error" for i in issues)
    word_failed = any(i.get("metric") == "word_count" for i in issues)

    return ok({
        "scope": "section",
        "section": section,
        "words": words,
        "target_words": target,
        "citations": refs,
        "cite_missing_placeholders": len(CITE_MISSING_RE.findall(content)),
        "issues": issues,
        "passed": bool(content.strip()) and not hard_failed and not word_failed,
    })


def run(args: Dict, root: Path) -> Dict:
    scope = args.get("scope", "section")
    try:
        if scope == "full":
            return _score_full(root)
        section = args.get("section")
        if not section:
            return fail("section is required when scope='section'")
        return _score_section(root, section)
    except ToolInputError as e:
        return fail(str(e))
    except FileNotFoundError as e:
        return fail(f"artifact not found: {e.filename}")


registry.register(registry.ToolSpec(
    name="score_draft",
    description=DESCRIPTION,
    input_schema=INPUT_SCHEMA,
    func=run,
))
