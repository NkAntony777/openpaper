#!/usr/bin/env python3
"""
ABOUTME: M4 experience distillation — pure offline heuristics over run_journal.jsonl +
ABOUTME: section_status.json. Each rule emits {lesson, evidence, confidence}; proposals land
ABOUTME: in <root>/lessons_proposed.md for HUMAN review (approved lessons move to
ABOUTME: lessons/approved/<name>.md and are injected into AGENTS.md by paper_map).
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_tools.common import SECTION_FILES, read_section_status

JOURNAL_REL = "run_journal.jsonl"
LESSONS_OUT_REL = "lessons_proposed.md"
LESSONS_APPROVED_REL = "lessons/approved"

MIN_WORDS_MARKERS = ("min_words", "section too short")
FIX_ROUNDS_THRESHOLD = 2
TOOL_FAILURE_STREAK = 3
SCORE_CALLS_FOR_PERSISTENCE = 2

# Fallback suggestion when a tool was unreliable: what to try instead.
TOOL_ALTERNATIVES = {
    "write_section": "revise_section on the existing draft",
    "revise_section": "write_section for a full rewrite",
    "score_draft": "read_artifact review of the section file",
    "search_literature": "the existing research/bibliography.json entries",
    "verify_claims": "manage_claims (record + verify persisted claims)",
    "manage_claims": "verify_claims for ad-hoc checks",
    "read_artifact": "pi's built-in read tool",
    "write_outline": "read_artifact on the current outline",
    "compile_draft": "re-run compile_draft once the draft is stable",
}

# section_status open-issue message -> score_draft metric name
_ISSUE_METRIC_PATTERNS = [
    (re.compile(r"section short|word", re.IGNORECASE), "word_count"),
    (re.compile(r"TODO|\[INSERT\]|\[expand\]|TBD|Lorem|placeholder", re.IGNORECASE), "placeholder"),
    (re.compile(r"empty or missing", re.IGNORECASE), "present"),
    (re.compile(r"citation", re.IGNORECASE), "citations"),
    (re.compile(r"structure|heading|outline", re.IGNORECASE), "structure"),
]


def _issue_metric(issue: str) -> str:
    for pattern, metric in _ISSUE_METRIC_PATTERNS:
        if pattern.search(issue):
            return metric
    return "general"


def _read_journal(root: Path) -> List[Dict]:
    p = Path(root) / JOURNAL_REL
    if not p.exists():
        return []
    entries: List[Dict] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
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


def _tool_starts(entries: List[Dict]) -> List[Dict]:
    """tool_execution_start entries with a parseable args payload."""
    out = []
    for e in entries:
        if e.get("type") != "tool_execution_start":
            continue
        summary = str(e.get("summary") or "")
        name, _, args_text = summary.partition(" ")
        args: Dict = {}
        try:
            parsed = json.loads(args_text)
            if isinstance(parsed, dict):
                args = parsed
        except json.JSONDecodeError:
            pass
        out.append({"tool": name, "args": args, "raw": summary})
    return out


def _tool_ends(entries: List[Dict]) -> List[Dict]:
    out = []
    for e in entries:
        if e.get("type") != "tool_execution_end":
            continue
        summary = str(e.get("summary") or "")
        name, _, status = summary.partition(" ")
        out.append({"tool": name, "error": status.strip().upper() == "ERROR", "raw": summary})
    return out


def _starts_by_tool(starts: List[Dict], tool: str) -> List[Dict]:
    return [s for s in starts if s["tool"] == tool]


def _section_of(start: Dict) -> Optional[str]:
    section = start["args"].get("section")
    return section if section in SECTION_FILES else None


# ----------------------------------------------------------------------------- rules


def _rule_min_words(entries: List[Dict]) -> List[Dict]:
    """Rule 1: a section whose write_section drafts were rejected >=2 times."""
    starts = _starts_by_tool(_tool_starts(entries), "write_section")
    ends = _tool_ends(entries)
    # pair sequentially: journal order interleaves start/end per call
    end_idx = 0
    rejected_by_section: Dict[str, int] = {}
    for s in starts:
        section = _section_of(s)
        paired = None
        while end_idx < len(ends):
            candidate = ends[end_idx]
            end_idx += 1
            if candidate["tool"] == "write_section":
                paired = candidate
                break
        if section and paired and paired["error"]:
            rejected_by_section[section] = rejected_by_section.get(section, 0) + 1

    journal_text = "\n".join(str(e.get("summary") or "") for e in entries)
    min_words_signals = sum(journal_text.count(m) for m in MIN_WORDS_MARKERS)

    lessons = []
    for section, n in sorted(rejected_by_section.items()):
        if n < FIX_ROUNDS_THRESHOLD:
            continue
        specific = min_words_signals > 0
        lessons.append(
            {
                "lesson": (
                    f"section {section}: first drafts ran short; aim for >=90% of the "
                    f"word target in the first write"
                    if specific
                    else f"section {section}: write_section was rejected {n}x; draft fuller, "
                    f"well-grounded first writes"
                ),
                "evidence": (
                    f"run_journal.jsonl: {n} write_section ERROR(s) for {section}; "
                    f"'min_words/too short' markers: {min_words_signals}"
                ),
                "confidence": 0.9 if specific else 0.6,
            }
        )
    return lessons


def _rule_tool_unreliable(entries: List[Dict]) -> List[Dict]:
    """Rule 2: a tool with >=3 consecutive error ends (streak resets on ok)."""
    streaks: Dict[str, int] = {}
    worst: Dict[str, int] = {}
    for end in _tool_ends(entries):
        tool = end["tool"]
        if end["error"]:
            streaks[tool] = streaks.get(tool, 0) + 1
            worst[tool] = max(worst.get(tool, 0), streaks[tool])
        else:
            streaks[tool] = 0

    lessons = []
    for tool, n in sorted(worst.items()):
        if n < TOOL_FAILURE_STREAK:
            continue
        alt = TOOL_ALTERNATIVES.get(tool, "another tool or manual inspection")
        lessons.append(
            {
                "lesson": f"tool {tool} was unreliable in this run; prefer alternative {alt}",
                "evidence": f"run_journal.jsonl: {n} consecutive tool_execution_end ERROR "
                f"entries for {tool}",
                "confidence": 0.8,
            }
        )
    return lessons


def _rule_metric_persists(entries: List[Dict], status: Dict) -> List[Dict]:
    """Rule 3: a score_draft metric whose issue survives a re-score after fixes."""
    starts = _starts_by_tool(_tool_starts(entries), "score_draft")
    score_calls: Dict[str, int] = {}
    for s in starts:
        section = _section_of(s)
        if section:
            score_calls[section] = score_calls.get(section, 0) + 1

    lessons = []
    sections = status.get("sections") or {}
    for section, calls in sorted(score_calls.items()):
        if calls < SCORE_CALLS_FOR_PERSISTENCE:
            continue
        entry = sections.get(section)
        if not isinstance(entry, dict):
            continue
        for issue in entry.get("open_issues") or []:
            metric = _issue_metric(str(issue))
            lessons.append(
                {
                    "lesson": (
                        f"metric {metric} persists for section {section}; expand evidence "
                        f"with search_literature before rewriting"
                    ),
                    "evidence": (
                        f"run_journal.jsonl: {calls} score_draft calls for {section}; "
                        f"section_status.json still lists: {str(issue)[:120]}"
                    ),
                    "confidence": 0.75,
                }
            )
    return lessons


def _rule_fix_rounds(entries: List[Dict]) -> List[Dict]:
    """Rule 4: sections that needed more than one fix round."""
    rounds_by_section: Dict[str, int] = {}
    for e in entries:
        if e.get("type") != "prompt":
            continue
        summary = str(e.get("summary") or "")
        m = re.search(r"session=fix-([a-z_]+)\b", summary)
        if m and m.group(1) in SECTION_FILES:
            rounds_by_section[m.group(1)] = rounds_by_section.get(m.group(1), 0) + 1

    lessons = []
    for section, n in sorted(rounds_by_section.items()):
        if n <= 1:
            continue
        lessons.append(
            {
                "lesson": (
                    f"section {section} needed {n} fix rounds; consider richer initial context"
                ),
                "evidence": f"run_journal.jsonl: {n} 'session=fix-{section}' prompt entries",
                "confidence": 0.85,
            }
        )
    return lessons


# ----------------------------------------------------------------------------- output


def _render_proposals(lessons: List[Dict]) -> str:
    lines = [
        "# Proposed lessons (auto-distilled, offline)",
        "",
        "HUMAN REVIEW: move approved lessons to lessons/approved/<name>.md — paper_map renders",
        "approved lessons into AGENTS.md under '## Lessons learned'.",
        "",
        f"Proposed: {len(lessons)}",
        "",
    ]
    for i, item in enumerate(lessons, 1):
        lines.append(f"## L{i} (confidence {item['confidence']:.2f})")
        lines.append(f"- lesson: {item['lesson']}")
        lines.append(f"- evidence: {item['evidence']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def distill(root) -> Dict:
    """Distill run_journal.jsonl + section_status.json into proposed lessons.

    Returns {"proposed": n, "lessons_path": str, "lessons": [...]} and writes
    <root>/lessons_proposed.md. Pure offline heuristics — never raises.
    """
    root = Path(root)
    entries = _read_journal(root)
    status = read_section_status(root)

    lessons: List[Dict] = []
    lessons.extend(_rule_min_words(entries))
    lessons.extend(_rule_tool_unreliable(entries))
    lessons.extend(_rule_metric_persists(entries, status))
    lessons.extend(_rule_fix_rounds(entries))
    lessons.sort(key=lambda item: -item["confidence"])

    out_path = root / LESSONS_OUT_REL
    out_path.write_text(_render_proposals(lessons), encoding="utf-8")

    return {
        "proposed": len(lessons),
        "lessons_path": str(out_path),
        "lessons": lessons,
        "journal_entries": len(entries),
    }


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="opendraft harness distill",
        description="Distill run_journal.jsonl + section_status.json into proposed lessons.",
    )
    parser.add_argument(
        "--root", type=Path, required=True, help="Paper output directory (the agent's working root)"
    )
    args = parser.parse_args(argv)

    summary = distill(args.root)
    payload = {
        "ok": True,
        "data": {
            "proposed": summary["proposed"],
            "lessons_path": summary["lessons_path"],
        },
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
