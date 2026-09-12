#!/usr/bin/env python3
"""
ABOUTME: T1 read_artifact — read any file under the output root (exploration/review primitive).
ABOUTME: Read-only, side-effect free, safe to call in parallel. Truncates to a high-signal window.
"""

from pathlib import Path
from typing import Dict

from agent_tools import registry
from agent_tools.envelope import ok, fail, ToolInputError, resolve_under_root

DEFAULT_LIMIT = 4000
MAX_LIMIT = 20000

DESCRIPTION = (
    "Read a file from the paper's output directory (research notes, outline, bibliography, "
    "section drafts, QA reports). Use to ground writing in real material before citing or "
    "claiming anything. Read-only. Content is truncated to a high-signal window — use offset "
    "to page through long files."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path relative to the output root, e.g. 'research/combined_research.md', "
                           "'drafts/00_formatted_outline.md', 'research/bibliography.json'.",
        },
        "offset": {
            "type": "integer",
            "description": "Character offset to start reading from (for paging). Default 0.",
        },
        "limit": {
            "type": "integer",
            "description": f"Max characters to return. Default {DEFAULT_LIMIT}, hard cap {MAX_LIMIT}.",
        },
    },
    "required": ["path"],
}


def run(args: Dict, root: Path) -> Dict:
    try:
        target = resolve_under_root(root, args.get("path"))
    except ToolInputError as e:
        return fail(str(e))

    if not target.exists() or not target.is_file():
        return fail(f"artifact not found: {args.get('path')}")

    try:
        offset = max(0, int(args.get("offset") or 0))
        limit = min(MAX_LIMIT, max(1, int(args.get("limit") or DEFAULT_LIMIT)))
    except (TypeError, ValueError):
        return fail("offset/limit must be integers")

    content = target.read_text(encoding="utf-8", errors="replace")
    snippet = content[offset:offset + limit]
    return ok({
        "path": args.get("path"),
        "content": snippet,
        "size": len(content),
        "offset": offset,
        "truncated": offset + limit < len(content),
    })


registry.register(registry.ToolSpec(
    name="read_artifact",
    description=DESCRIPTION,
    input_schema=INPUT_SCHEMA,
    func=run,
))
