#!/usr/bin/env python3
"""
ABOUTME: T8 write_outline — NON-LINEAR outline revision. Overwrites
ABOUTME: drafts/00_formatted_outline.md (merge=true does conservative ## -heading
ABOUTME: replacement) and syncs checkpoint.json's formatter_output. For restructuring
ABOUTME: the paper plan mid-writing — NOT for writing body text.
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agent_tools import registry
from agent_tools.common import read_checkpoint, write_checkpoint
from agent_tools.envelope import ToolInputError, fail, ok, resolve_under_root

OUTLINE_REL = "drafts/00_formatted_outline.md"
CHECKPOINT_FIELD = "formatter_output"

HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")

DESCRIPTION = (
    "Write or revise the paper's formatted outline (drafts/00_formatted_outline.md). "
    "This is the NON-LINEAR structure-control tool: use it when writing reveals the outline "
    "needs restructuring — e.g. a section must be split/merged/reordered, subsections added, "
    "or the argument flow changed. After a successful write the outline is synced into "
    "checkpoint.json (formatter_output), so downstream phases see the new structure. "
    "merge=true conservatively replaces only the paragraphs whose '## ' heading also appears "
    "in the new content (everything else is preserved; new headings are appended). "
    "Do NOT use write_outline to write section body text — that is write_section's job — and "
    "do NOT use it when the existing outline is fine."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "minLength": 1,
            "description": "Full markdown outline content (title line + '## ' headed blocks).",
        },
        "merge": {
            "type": "boolean",
            "default": False,
            "description": "Conservative merge: replace only '## '-headed blocks whose heading "
            "also exists in the current outline; keep all other blocks; append "
            "new headings. Default false = full overwrite.",
        },
    },
    "required": ["content"],
}


def _split_blocks(text: str) -> List[Tuple[Optional[str], str]]:
    """Split outline text into (heading, block) pairs. Heading None = preamble before the
    first '## ' line. A block runs until the next '## ' heading."""
    blocks: List[Tuple[Optional[str], str]] = []
    current_head: Optional[str] = None
    current_lines: List[str] = []
    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            blocks.append((current_head, "\n".join(current_lines).strip("\n")))
            current_head = m.group(1)
            current_lines = [line]
        else:
            current_lines.append(line)
    blocks.append((current_head, "\n".join(current_lines).strip("\n")))
    # drop empty pseudo-blocks except a leading empty preamble
    cleaned: List[Tuple[Optional[str], str]] = []
    for i, (head, body) in enumerate(blocks):
        if head is None and i > 0:
            continue
        if head is not None and not body.strip():
            continue
        cleaned.append((head, body))
    return cleaned


def _merge_content(existing: str, new: str) -> Tuple[str, int, int]:
    """Conservative heading-keyed merge. Returns (merged_text, replaced, added)."""
    old_blocks = _split_blocks(existing)
    new_blocks = _split_blocks(new)
    new_by_head = {h: b for h, b in new_blocks if h is not None}

    out: List[str] = []
    replaced = 0
    seen = set()
    for head, body in old_blocks:
        if head is None:
            if body.strip():
                out.append(body)
            continue
        if head in new_by_head:
            out.append(new_by_head[head])
            replaced += 1
            seen.add(head)
        else:
            out.append(body)
    added = 0
    for head, body in new_blocks:
        if head is not None and head not in seen:
            out.append(body)
            added += 1
    merged = "\n\n".join(b for b in out if b.strip())
    return merged + "\n", replaced, added


def run(args: Dict, root: Path) -> Dict:
    content = args.get("content")
    if not isinstance(content, str) or not content.strip():
        return fail("content must be a non-empty string")

    try:
        target = resolve_under_root(root, OUTLINE_REL)
    except ToolInputError as e:
        return fail(str(e))

    merge = bool(args.get("merge", False))
    replaced = added = 0
    if merge and target.exists():
        existing = target.read_text(encoding="utf-8")
        content, replaced, added = _merge_content(existing, content)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    # read-modify-write checkpoint sync (formatter_output)
    checkpoint_state = "absent"
    data = read_checkpoint(root)
    if data is not None:
        data[CHECKPOINT_FIELD] = content
        write_checkpoint(root, data)
        checkpoint_state = "updated"

    return ok(
        {
            "path": OUTLINE_REL,
            "words": len(content.split()),
            "merged": merge,
            "replaced_blocks": replaced,
            "added_blocks": added,
            "checkpoint": checkpoint_state,
        }
    )


registry.register(
    registry.ToolSpec(
        name="write_outline",
        description=DESCRIPTION,
        input_schema=INPUT_SCHEMA,
        func=run,
    )
)
