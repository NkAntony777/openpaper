#!/usr/bin/env python3
"""
ABOUTME: M4 eval suite — offline five-metric evaluation of a paper output directory.
ABOUTME: quality score, FactCheck cleanliness, citation authenticity, token cost, fix
ABOUTME: rounds. CI runs this over tests/eval_gold/ with per-fixture threshold specs.
"""

import json
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from harness.acceptance import run_finish_acceptance
from harness.journal_distill import JOURNAL_REL

GOLD_DIR_REL = "tests/eval_gold"
MANIFEST_NAME = "manifest.json"


@dataclass
class EvalMetrics:
    quality_score: Optional[int]
    factcheck_clean: bool
    citation_rate: float
    token_cost: float
    fix_rounds: int
    forbidden_hits: int
    cite_missing: int
    passed: bool
    gaps: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


def _journal_entries(root: Path) -> List[Dict]:
    p = Path(root) / JOURNAL_REL
    if not p.exists():
        return []
    out = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _token_cost(entries: List[Dict]) -> float:
    """Best-effort: last get_session_stats-like summary, else 0.

    Driver journal lines are {type, summary}; cost is not a first-class field, so we
    scrape `cost=N` from prompt/session summaries (PiDriver writes
    `budget(cost=...)` which is the cap, not spend). Session stats land as
    type=response summaries when present; we also accept a numeric 'cost' key.
    """
    last = 0.0
    for e in entries:
        if isinstance(e.get("cost"), (int, float)):
            last = float(e["cost"])
            continue
        summary = str(e.get("summary") or "")
        m = re.search(r"\bspent[=:](\d+(?:\.\d+)?)", summary)
        if m:
            last = float(m.group(1))
    return last


def _fix_rounds(entries: List[Dict]) -> int:
    n = 0
    for e in entries:
        if e.get("type") != "prompt":
            continue
        if re.search(r"session=fix-", str(e.get("summary") or "")):
            n += 1
    return n


def evaluate_root(root) -> EvalMetrics:
    """Score one paper directory. Never raises — missing artifacts become gaps."""
    root = Path(root)
    quality: Optional[int] = None
    try:
        from agent_tools.score import run as score_run

        verdict = score_run({"scope": "full"}, root)
        if verdict.get("ok"):
            total = verdict["data"].get("total")
            quality = int(total) if total is not None else None
        else:
            quality = None
    except Exception:
        quality = None

    gate = run_finish_acceptance(root)
    entries = _journal_entries(root)
    metrics = EvalMetrics(
        quality_score=quality,
        factcheck_clean=gate.claims_clean,
        citation_rate=gate.citation_rate,
        token_cost=_token_cost(entries),
        fix_rounds=_fix_rounds(entries),
        forbidden_hits=len(gate.forbidden_hits),
        cite_missing=gate.cite_missing,
        passed=gate.passed,
        gaps=list(gate.gaps),
    )
    return metrics


def check_thresholds(metrics: EvalMetrics, spec: Dict) -> List[str]:
    """Return human-readable failures against a fixture spec. Empty = pass.

    Spec keys (all optional):
      min_quality, require_factcheck_clean, min_citation_rate, max_forbidden_hits,
      max_cite_missing, expect_passed, max_fix_rounds
    """
    failures = []
    if "min_quality" in spec:
        floor = spec["min_quality"]
        if metrics.quality_score is None or metrics.quality_score < floor:
            failures.append(
                f"quality_score {metrics.quality_score} < min_quality {floor}"
            )
    if spec.get("require_factcheck_clean") and not metrics.factcheck_clean:
        failures.append("factcheck_clean is False")
    if spec.get("require_factcheck_dirty") and metrics.factcheck_clean:
        failures.append("expected factcheck_clean False (dirty fixture)")
    if "min_citation_rate" in spec and metrics.citation_rate < spec["min_citation_rate"]:
        failures.append(
            f"citation_rate {metrics.citation_rate:.2f} < {spec['min_citation_rate']}"
        )
    if "max_citation_rate" in spec and metrics.citation_rate > spec["max_citation_rate"]:
        failures.append(
            f"citation_rate {metrics.citation_rate:.2f} > {spec['max_citation_rate']}"
        )
    if "max_forbidden_hits" in spec and metrics.forbidden_hits > spec["max_forbidden_hits"]:
        failures.append(
            f"forbidden_hits {metrics.forbidden_hits} > {spec['max_forbidden_hits']}"
        )
    if "min_forbidden_hits" in spec and metrics.forbidden_hits < spec["min_forbidden_hits"]:
        failures.append(
            f"forbidden_hits {metrics.forbidden_hits} < {spec['min_forbidden_hits']}"
        )
    if "max_cite_missing" in spec and metrics.cite_missing > spec["max_cite_missing"]:
        failures.append(
            f"cite_missing {metrics.cite_missing} > {spec['max_cite_missing']}"
        )
    if "expect_passed" in spec and bool(metrics.passed) != bool(spec["expect_passed"]):
        failures.append(f"passed={metrics.passed} expected {spec['expect_passed']}")
    if "max_fix_rounds" in spec and metrics.fix_rounds > spec["max_fix_rounds"]:
        failures.append(f"fix_rounds {metrics.fix_rounds} > {spec['max_fix_rounds']}")
    return failures


def load_manifest(gold_dir: Path) -> List[Dict]:
    p = Path(gold_dir) / MANIFEST_NAME
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    fixtures = data.get("fixtures") if isinstance(data, dict) else data
    return fixtures if isinstance(fixtures, list) else []


def evaluate_gold_set(gold_dir) -> Dict:
    """Run every fixture in gold_dir/manifest.json. Returns {ok, results: [...]}."""
    gold_dir = Path(gold_dir)
    fixtures = load_manifest(gold_dir)
    results = []
    all_ok = True
    for spec in fixtures:
        fid = spec.get("id") or spec.get("path")
        if not fid:
            results.append({"id": None, "ok": False, "failures": ["fixture missing id"]})
            all_ok = False
            continue
        src = gold_dir / fid
        if not src.is_dir():
            results.append({"id": fid, "ok": False, "failures": [f"missing fixture dir {src}"]})
            all_ok = False
            continue
        # Copy: score_draft persists section_status.json and must not dirty the gold tree.
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / fid
            shutil.copytree(src, dest)
            metrics = evaluate_root(dest)
        failures = check_thresholds(metrics, spec.get("gates") or spec)
        ok = not failures
        all_ok = all_ok and ok
        results.append({
            "id": fid,
            "ok": ok,
            "metrics": metrics.to_dict(),
            "failures": failures,
        })
    return {"ok": all_ok, "results": results, "n": len(results)}
