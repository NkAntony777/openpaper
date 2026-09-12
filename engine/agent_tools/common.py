#!/usr/bin/env python3
"""
ABOUTME: Shared helpers for agent tools: checkpoint sync, snapshots, word targets,
ABOUTME: bibliography access. All operations are relative to the output root.
"""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

CHECKPOINT_NAME = "checkpoint.json"
BIBLIOGRAPHY_REL = "research/bibliography.json"
SNAPSHOT_DIR_REL = "drafts/.snapshots"

# Standard section -> output file + checkpoint field + word_targets key.
# Body sections also feed checkpoint "body_output" (rebuilt on every write).
SECTION_FILES: Dict[str, Dict] = {
    "introduction": {
        "file": "drafts/01_introduction.md",
        "ckpt": "intro_output",
        "wt_key": "introduction",
    },
    "literature_review": {
        "file": "drafts/02_1_literature_review.md",
        "ckpt": "lit_review_output",
        "wt_key": "literature_review",
        "body": True,
    },
    "methodology": {
        "file": "drafts/02_2_methodology.md",
        "ckpt": "methodology_output",
        "wt_key": "methodology",
        "body": True,
    },
    "results": {
        "file": "drafts/02_3_analysis_results.md",
        "ckpt": "results_output",
        "wt_key": "results",
        "body": True,
    },
    "discussion": {
        "file": "drafts/02_4_discussion.md",
        "ckpt": "discussion_output",
        "wt_key": "discussion",
        "body": True,
    },
    "conclusion": {
        "file": "drafts/03_conclusion.md",
        "ckpt": "conclusion_output",
        "wt_key": "conclusion",
    },
    "appendices": {
        "file": "drafts/04_appendices.md",
        "ckpt": "appendix_output",
        "wt_key": "appendices",
    },
}

BODY_SECTIONS: List[str] = ["literature_review", "methodology", "results", "discussion"]

CUSTOM_SECTIONS_DIR = "drafts/custom_sections"


def read_checkpoint(root: Path) -> Optional[Dict]:
    p = Path(root) / CHECKPOINT_NAME
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write_checkpoint(root: Path, data: Dict) -> None:
    (Path(root) / CHECKPOINT_NAME).write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def parse_target_max(raw) -> int:
    """Parse a word-target spec like '600-800', '3,000-5,000' or 800 into its max. 0 = no target."""
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    nums = re.findall(r"[\d,]+", str(raw))
    if not nums:
        return 0
    return int(nums[-1].replace(",", ""))


def word_target_max(root: Path, wt_key: str) -> int:
    ckpt = read_checkpoint(root)
    raw = (ckpt.get("word_targets") or {}).get(wt_key) if ckpt else None
    return parse_target_max(raw)


def bibliography_ids(root: Path) -> Set[str]:
    p = Path(root) / BIBLIOGRAPHY_REL
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return {c.get("id", "") for c in data.get("citations", []) if c.get("id")}


def snapshot_existing(root: Path, rel_path: str) -> Optional[str]:
    """Copy an existing artifact into drafts/.snapshots/. Returns snapshot rel path or None."""
    src = Path(root) / rel_path
    if not src.exists():
        return None
    snap_dir = Path(root) / SNAPSHOT_DIR_REL
    snap_dir.mkdir(parents=True, exist_ok=True)
    name = f"{src.stem}__{datetime.now().strftime('%Y%m%d_%H%M%S')}{src.suffix}"
    shutil.copy2(src, snap_dir / name)
    return f"{SNAPSHOT_DIR_REL}/{name}"


def rebuild_body_output(root: Path) -> Optional[str]:
    """Concatenate the four body section files (canonical order) for checkpoint body_output."""
    root = Path(root)
    parts = []
    for section in BODY_SECTIONS:
        f = root / SECTION_FILES[section]["file"]
        if f.exists():
            parts.append(f.read_text(encoding="utf-8"))
    return "\n\n".join(parts) if parts else None


def sync_checkpoint_section(root: Path, section: str, content: str) -> Dict:
    """
    Write a section's content into checkpoint.json (its *_output field) and, for body
    sections, rebuild body_output from the section files on disk.

    Returns {"checkpoint": "updated"|"absent", "body": bool}.
    No checkpoint on disk is not an error (tools are usable mid-pipeline).
    """
    data = read_checkpoint(root)
    if data is None:
        return {"checkpoint": "absent", "body": False}
    meta = SECTION_FILES.get(section) or {}
    if meta.get("ckpt"):
        data[meta["ckpt"]] = content
    body_updated = False
    if meta.get("body"):
        body = rebuild_body_output(root)
        if body is not None:
            data["body_output"] = body
            body_updated = True
    write_checkpoint(root, data)
    return {"checkpoint": "updated", "body": body_updated}
