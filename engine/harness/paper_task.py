#!/usr/bin/env python3
"""
ABOUTME: Paper orchestrator (design doc §5.3): runs the non-linear writing loop end to
ABOUTME: end — sections in outline order, global review, targeted fix sessions, full
ABOUTME: score (+ optional compile). The orchestration sequence is policy here; within a
ABOUTME: section the model decides how to write (section_task/review_task prompts).
ABOUTME: Resume semantics: a section whose ledger entry is status written/revised AND
ABOUTME: passed=true is skipped, so rerunning a paper directory is idempotent.
"""

import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_tools.common import (
    SECTION_FILES,
    read_section_status,
    sync_checkpoint_section,
    word_target_max,
)
from harness.driver import BudgetConfig, DriverResult, PiDriver
from harness.review_task import build_review_prompt
from harness.section_task import build_fix_prompt, build_section_prompt

DEFAULT_PAPER_SECTIONS = [
    "introduction",
    "literature_review",
    "methodology",
    "results",
    "discussion",
    "conclusion",
]

GLOBAL_ISSUES_NAME = "global_issues.md"

# ----------------------------------------------------------------------------- budget


@dataclass
class PaperBudget:
    """Envelope over the whole paper run. Each pi session gets a per-phase cap that is
    additionally clamped by the remaining total budget."""

    section_cost: float = 0.35
    review_cost: float = 0.15
    fix_cost: float = 0.15
    total_cost: float = 1.5
    max_fix_rounds: int = 1


@dataclass
class PaperResult:
    ok: bool
    sections_completed: List[str] = field(default_factory=list)
    sections_skipped: List[str] = field(default_factory=list)
    review_ok: bool = False
    issues_found: int = 0
    issues_fixed_report: Dict[str, str] = field(default_factory=dict)
    full_score: Optional[int] = None
    claims_clean: bool = True
    forbidden_hits: int = 0
    citation_rate: float = 1.0
    lessons_proposed: int = 0
    finish_gaps: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    total_cost: float = 0.0
    journal_paths: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


# ------------------------------------------------------------------ global issue parse

GI_HEADER_RE = re.compile(
    r"^##\s+(GI-\d+)\s*(?:\[([^\]]*)\])?(?:\s*scope:\s*([^\s]+))?", re.IGNORECASE
)
ISSUE_LINE_RE = re.compile(r"^Issue:\s*(.*)$", re.IGNORECASE)
FIX_LINE_RE = re.compile(r"^Suggested fix:\s*(.*)$", re.IGNORECASE)


def parse_global_issues(text: str) -> List[Dict]:
    """Parse global_issues.md into [{id, severity, scope, issue, fix}].

    Tolerant by design: missing severity defaults to medium, missing scope to global,
    missing Suggested fix to "", paragraphs without a GI header are ignored.
    """
    issues: List[Dict] = []
    if not text:
        return issues
    current: Optional[Dict] = None
    for raw in text.splitlines():
        line = raw.strip()
        m = GI_HEADER_RE.match(line)
        if m:
            if current is not None:
                issues.append(current)
            severity = (m.group(2) or "medium").strip().lower() or "medium"
            scope = (m.group(3) or "global").strip() or "global"
            current = {
                "id": m.group(1),
                "severity": severity,
                "scope": scope,
                "issue": "",
                "fix": "",
            }
            continue
        if current is None:
            continue
        im = ISSUE_LINE_RE.match(line)
        if im:
            current["issue"] = im.group(1).strip()
            continue
        fm = FIX_LINE_RE.match(line)
        if fm:
            current["fix"] = fm.group(1).strip()
    if current is not None:
        issues.append(current)
    return [i for i in issues if i["issue"] or i["fix"]]


def _group_issues_by_section(
    issues: List[Dict], planned: List[str]
) -> "tuple[Dict[str, List[Dict]], List[str]]":
    """Bucket issues per section. scope: <section> goes to that section; scope: global is
    distributed to the sections its Suggested fix names (word-boundary match); anything
    unresolvable is skipped with a note."""
    groups: Dict[str, List[Dict]] = {}
    notes: List[str] = []
    for it in issues:
        scope = it.get("scope", "global")
        if scope == "global":
            named = [
                s
                for s in planned
                if re.search(rf"\b{re.escape(s)}\b", it.get("fix") or "", re.IGNORECASE)
            ]
            if not named:
                notes.append(
                    f"{it['id']}: skipped — global-scope issue names no section in its fix"
                )
                continue
            targets = named
        elif scope in SECTION_FILES:
            targets = [scope]
        else:
            notes.append(f"{it['id']}: skipped — unknown scope '{scope}'")
            continue
        for t in targets:
            if t not in planned:
                notes.append(f"{it['id']}: skipped — '{t}' is not in this run's plan")
                continue
            groups.setdefault(t, []).append(it)
    return groups, notes


# ----------------------------------------------------------------------------- helpers


def _session_cost(result: DriverResult) -> float:
    try:
        return float((result.stats or {}).get("cost") or 0)
    except (TypeError, ValueError):
        return 0.0


def _default_plan(root: Path) -> List[str]:
    """Outline-order sections; appendices join only when its word target is a real number."""
    planned = list(DEFAULT_PAPER_SECTIONS)
    if word_target_max(root, SECTION_FILES["appendices"]["wt_key"]) > 0:
        planned.append("appendices")
    return planned


def _section_is_done(root: Path, section: str) -> bool:
    entry = (read_section_status(root).get("sections") or {}).get(section)
    if not isinstance(entry, dict):
        return False
    return entry.get("status") in ("written", "revised") and entry.get("passed") is True


# --------------------------------------------------------------------------------- run


def run_paper(
    root,
    sections: Optional[List[str]] = None,
    driver_factory: Optional[Callable] = None,
    budget: Optional[PaperBudget] = None,
    model: Optional[str] = None,
    max_turns: int = 40,
    compile_at_end: bool = False,
    min_full_score: Optional[int] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> PaperResult:
    """Orchestrate a full paper run. Never raises — all failures land in the result."""
    root = Path(root)
    driver_factory = driver_factory or PiDriver
    budget = budget or PaperBudget()
    result = PaperResult(ok=True)

    def _say(msg: str) -> None:
        if progress is not None:
            progress(msg)

    def _warn(msg: str) -> None:
        result.warnings.append(msg)
        _say(f"warning: {msg}")

    consumed = 0.0
    no_cost_sessions: List[str] = []

    def _session_cap(phase_cost: float) -> float:
        return min(phase_cost, budget.total_cost - consumed)

    def _new_driver(cap: float):
        return driver_factory(
            root=root,
            model=model,
            budget=BudgetConfig(max_cost_usd=cap, max_turns=max_turns),
        )

    def _record(result_: DriverResult, name: str) -> None:
        nonlocal consumed
        consumed += _session_cost(result_)
        result.total_cost = consumed
        if result_.journal_path:
            result.journal_paths.append(result_.journal_path)
        if result_.budget_exceeded:
            _warn(f"session '{name}' exceeded its budget ({result_.reason})")
        if not (result_.stats or {}).get("cost"):
            no_cost_sessions.append(name)

    # ------------------------------------------------------------ 1. plan
    if sections is not None:
        planned = [s for s in sections if s in SECTION_FILES]
        dropped = sorted(set(sections) - set(planned))
        if dropped:
            _warn(f"unknown sections dropped from plan: {dropped}")
    else:
        planned = _default_plan(root)
    _say(f"plan: {', '.join(planned)}")

    # --------------------------------------------------- 2. sections (resume-aware)
    failed_sections: List[str] = []
    for section in planned:
        if _section_is_done(root, section):
            result.sections_skipped.append(section)
            _say(f"section {section}: skipped (already written and passed)")
            continue
        cap = _session_cap(budget.section_cost)
        if cap <= 0:
            remaining = [
                s
                for s in planned
                if s not in result.sections_skipped and s not in result.sections_completed
            ]
            _warn(f"total budget exhausted after ${consumed:.2f}; not attempted: {remaining}")
            failed_sections.extend(remaining)
            break
        driver = _new_driver(cap)
        run_result = driver.run(build_section_prompt(root, section), name=f"section-{section}")
        _record(run_result, f"section-{section}")
        if not run_result.ok:
            failed_sections.append(section)
            _warn(f"section {section} failed: {run_result.reason}")
            continue
        result.sections_completed.append(section)
        # finalize: same acceptance as the single-section CLI path (design §5.2/T8)
        try:
            from agent_tools.score import run as score_run

            section_file = root / SECTION_FILES[section]["file"]
            if section_file.exists():
                sync_checkpoint_section(root, section, section_file.read_text(encoding="utf-8"))
            verdict = score_run({"scope": "section", "section": section}, root)
            if verdict.get("ok"):
                passed = verdict["data"].get("passed")
                _say(f"section {section}: written (score {'pass' if passed else 'FAIL'})")
            else:
                _warn(f"section {section} acceptance scoring failed: {verdict.get('error')}")
        except Exception as e:  # acceptance must not kill the run
            _warn(f"section {section} acceptance error: {type(e).__name__}: {e}")

    # ------------------------------------------------------- 3. global review
    review_text = ""
    cap = _session_cap(budget.review_cost)
    if cap <= 0:
        _warn(f"review skipped: budget exhausted (spent ${consumed:.2f})")
    else:
        driver = _new_driver(cap)
        run_result = driver.run(build_review_prompt(root), name="global-review")
        _record(run_result, "global-review")
        settled = (run_result.settled_text or "").strip()
        if run_result.ok and settled:
            (root / GLOBAL_ISSUES_NAME).write_text(settled, encoding="utf-8")
            review_text = settled
            result.review_ok = True
        elif run_result.ok:
            result.ok = False
            _warn("review settled but produced no text — treating as failed review")
        else:
            result.ok = False
            _warn(f"review failed: {run_result.reason}")

    issues = parse_global_issues(review_text)
    result.issues_found = len(issues)
    _say(f"review: {result.issues_found} issue(s) found")

    # --------------------------------------------- 4. targeted fix sessions
    if issues:
        groups, notes = _group_issues_by_section(issues, planned)
        for note in notes:
            _warn(note)
        confirmed: set = set()
        fix_budget_done = False
        for _rnd in range(budget.max_fix_rounds):
            if fix_budget_done:
                break
            pending = {s: ii for s, ii in groups.items() if s not in confirmed}
            if not pending:
                break
            for section, sec_issues in pending.items():
                if not (root / SECTION_FILES[section]["file"]).exists():
                    result.issues_fixed_report[section] = "skipped: no section file on disk"
                    confirmed.add(section)  # nothing a fix session can act on
                    continue
                cap = _session_cap(budget.fix_cost)
                if cap <= 0:
                    _warn(f"fix phase stopped: budget exhausted (spent ${consumed:.2f})")
                    fix_budget_done = True
                    break
                driver = _new_driver(cap)
                run_result = driver.run(
                    build_fix_prompt(root, section, sec_issues), name=f"fix-{section}"
                )
                _record(run_result, f"fix-{section}")
                report = (
                    run_result.settled_text or "(no report text)"
                    if run_result.ok
                    else f"session failed: {run_result.reason}"
                )
                # Close the loop on disk truth, not self-report: re-score the section
                # after the fix and only confirm when it actually passes.
                try:
                    from agent_tools.score import run as score_run

                    verdict = score_run({"scope": "section", "section": section}, root)
                    if verdict.get("ok"):
                        passed = bool(verdict["data"].get("passed"))
                        rescore = f"re-score: {'pass' if passed else 'FAIL'}"
                        if passed:
                            confirmed.add(section)
                    else:
                        rescore = f"re-score failed: {verdict.get('error')}"
                except Exception as e:
                    rescore = f"re-score error: {type(e).__name__}: {e}"
                result.issues_fixed_report[section] = f"{report} | {rescore}"
                _say(f"fix {section}: {rescore} ({len(sec_issues)} issue(s))")
        for section in groups:
            if section in confirmed:
                continue
            note = result.issues_fixed_report.get(section, "")
            if str(note).startswith("skipped"):
                continue
            gap = f"section {section} still failing re-score after fix round(s)"
            result.finish_gaps.append(gap)
            result.ok = False
            _warn(f"fix: {gap}")

    # ---------------------------------------------------- 5. full-score acceptance
    try:
        from agent_tools.score import run as score_run

        verdict = score_run({"scope": "full"}, root)
        if verdict.get("ok"):
            result.full_score = verdict["data"].get("total")
            _say(f"full score: {result.full_score}")
        else:
            _warn(f"full scoring failed: {verdict.get('error')}")
    except Exception as e:
        _warn(f"full scoring error: {type(e).__name__}: {e}")

    # ------------------------------------------ 5b. finish acceptance (M3 T8, M5 hardened)
    try:
        from harness.acceptance import run_finish_acceptance

        gate = run_finish_acceptance(
            root, min_full_score=min_full_score, full_score=result.full_score
        )
        result.claims_clean = gate.claims_clean
        result.forbidden_hits = len(gate.forbidden_hits)
        result.citation_rate = gate.citation_rate
        result.finish_gaps = list(gate.gaps) + result.finish_gaps  # keep fix-loop gaps
        if gate.gaps:
            for g in gate.gaps:
                _warn(f"finish: {g}")
        if not gate.passed:
            result.ok = False
            _say(f"finish gate FAIL ({len(gate.gaps)} gap(s))")
        else:
            _say("finish gate: pass")
    except Exception as e:
        _warn(f"finish acceptance error: {type(e).__name__}: {e}")

    if no_cost_sessions:
        _warn(
            f"{len(no_cost_sessions)} session(s) reported no cost "
            f"({', '.join(no_cost_sessions[:5])}) — cost breakers were blind there"
        )

    # ------------------------------------------ 5c. distill lessons (M4)
    try:
        from harness.journal_distill import distill

        if (root / "run_journal.jsonl").exists():
            summary = distill(root)
            result.lessons_proposed = int(summary.get("proposed") or 0)
            _say(f"distill: {result.lessons_proposed} proposed lesson(s)")
    except Exception as e:
        _warn(f"distill error: {type(e).__name__}: {e}")

    # ------------------------------------------------------------- 6. compile
    if compile_at_end:
        try:
            from agent_tools.compile_export import run as compile_run

            verdict = compile_run({"format": "all"}, root)
            if verdict.get("ok"):
                _say("compile: ok")
            else:
                _warn(f"compile failed: {verdict.get('error')}")
        except Exception as e:
            _warn(f"compile error: {type(e).__name__}: {e}")

    if failed_sections:
        result.ok = False
    return result
