#!/usr/bin/env python3
"""M5 hardening tests (audit remediation): finish-gate completeness (presence, word
floor, quality floor, negation-aware forbidden scan), revise invalidating passed,
fix-session re-score closure, session_end cost telemetry, and threshold keys."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

from agent_tools.common import read_section_status, update_section_status, write_checkpoint
from harness.acceptance import match_forbidden_claims, run_finish_acceptance
from harness.eval_suite import EvalMetrics, check_thresholds, evaluate_root


def _intro(root: Path, text: str) -> None:
    drafts = root / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "01_introduction.md").write_text(text, encoding="utf-8")


def _bib(root: Path) -> None:
    (root / "research").mkdir(parents=True, exist_ok=True)
    (root / "research" / "bibliography.json").write_text(
        json.dumps({"citations": [{"id": "cite_001"}]}), encoding="utf-8")


# ---------------------------------------------------------- finish gate: presence


def test_gate_fails_missing_planned_section(tmp_path):
    write_checkpoint(tmp_path, {"topic": "T", "word_targets": {"introduction": 80}})
    # no drafts at all
    gate = run_finish_acceptance(tmp_path)
    assert gate.passed is False
    assert gate.missing_sections == ["introduction"]
    assert any("missing on disk" in g for g in gate.gaps)


def test_gate_fails_below_floor_section(tmp_path):
    write_checkpoint(tmp_path, {"topic": "T", "word_targets": {"introduction": 80}})
    _intro(tmp_path, "too short draft")
    gate = run_finish_acceptance(tmp_path)
    assert gate.passed is False
    assert gate.thin_sections and gate.thin_sections[0]["section"] == "introduction"
    assert gate.thin_sections[0]["words"] == 3


def test_gate_passes_present_floor_met(tmp_path):
    write_checkpoint(tmp_path, {"topic": "T", "word_targets": {"introduction": 20}})
    _bib(tmp_path)
    _intro(tmp_path, "Dense passage retrieval encodes queries and documents "
                    "independently {cite_001}. " + "It scales. " * 8)
    gate = run_finish_acceptance(tmp_path)
    assert gate.missing_sections == []
    assert gate.thin_sections == []


def test_gate_ignores_non_section_target_keys(tmp_path):
    # 'min_citations' is a target knob, not a section — must not be demanded on disk
    write_checkpoint(tmp_path, {"topic": "T",
                                "word_targets": {"introduction": 20, "min_citations": 1}})
    _intro(tmp_path, "word " * 30)
    gate = run_finish_acceptance(tmp_path)
    assert gate.missing_sections == []


# ------------------------------------------------------- finish gate: quality floor


def test_gate_enforces_min_full_score(tmp_path):
    write_checkpoint(tmp_path, {"topic": "T"})
    gate = run_finish_acceptance(tmp_path, min_full_score=75, full_score=62)
    assert gate.passed is False
    assert gate.quality_gap and "62" in gate.quality_gap


def test_gate_quality_floor_needs_score(tmp_path):
    write_checkpoint(tmp_path, {"topic": "T"})
    gate = run_finish_acceptance(tmp_path, min_full_score=75, full_score=None)
    assert gate.passed is False
    assert "unavailable" in (gate.quality_gap or "")


def test_gate_no_floor_by_default(tmp_path):
    write_checkpoint(tmp_path, {"topic": "T"})
    gate = run_finish_acceptance(tmp_path)
    assert gate.quality_gap is None


# --------------------------------------------- forbidden scan: negation exemption


def test_forbidden_negated_sentence_exempt(tmp_path):
    write_checkpoint(tmp_path, {
        "topic": "T",
        "research_brief": {"forbidden_claims": ["causal relationship between proximity and friendship"]},
    })
    _intro(tmp_path, (
        "We do not claim a causal relationship between proximity and friendship.\n"
        "Our design is purely correlational."
    ))
    gate = run_finish_acceptance(tmp_path)
    assert gate.forbidden_hits == []
    assert gate.passed is True


def test_forbidden_positive_sentence_still_fires():
    hits = match_forbidden_claims(
        "This paper asserts a causal relationship between proximity and friendship.",
        ["causal relationship between proximity and friendship"],
    )
    assert len(hits) == 1


# ------------------------------------------------------- revise invalidates passed


def test_revise_invalidates_section_passed(tmp_path, monkeypatch):
    from agent_tools import registry
    import agent_tools.revise as revise_mod

    # hermetic: a unique find_replace must never touch the LLM path
    def _no_llm(*a, **kw):
        raise AssertionError("LLM revise must not be called for a unique find_replace")
    monkeypatch.setattr(revise_mod, "_llm_revise", _no_llm)

    write_checkpoint(tmp_path, {"topic": "T", "word_targets": {"introduction": 20}})
    _intro(tmp_path, "Original introduction text that is long enough to survive "
                     "any floor checks easily, with a different second clause here.")
    update_section_status(tmp_path, "introduction", status="written", passed=True,
                          updated_at="2026-01-01")
    spec = registry.get_tool("revise_section")
    r = spec.func({"section": "introduction",
                   "instructions": "update the opening wording",
                   "find_replace": [
                       {"find": "Original introduction", "replace": "Revised introduction"},
                   ]}, tmp_path)
    assert r.get("ok"), r
    entry = read_section_status(tmp_path)["sections"]["introduction"]
    assert entry["passed"] is False


# --------------------------------------------------- fix re-score loop closure


def test_fix_unconfirmed_section_fails_run(tmp_path):
    from harness.paper_task import PaperBudget, run_paper

    root = tmp_path / "out"
    root.mkdir()
    write_checkpoint(root, {"topic": "T", "academic_level": "master",
                            "citation_style": "apa", "language": "en", "word_targets": {}})
    update_section_status(root, "introduction", status="written", passed=True,
                          updated_at="2026-01-01")
    (root / "drafts").mkdir()
    (root / "drafts" / "01_introduction.md").write_text("word " * 300, "utf-8")

    review = (
        "## GI-1 [high] scope: introduction\n"
        "Issue: the framing contradicts the outline.\n"
        "Suggested fix: rewrite the framing in introduction.\n"
    )
    REVIEW = review
    calls = []

    class _Driver:
        def __init__(self, **kwargs):
            self.root = kwargs["root"]

        def run(self, prompt, name):
            from harness.driver import DriverResult
            calls.append(name)
            # the fix session BREAKS the section: a hard placeholder fails re-score
            if name.startswith("fix-"):
                (self.root / "drafts" / "01_introduction.md").write_text(
                    "TODO: rewrite this", "utf-8")
                return DriverResult(ok=True, reason="settled", stats={"cost": 0.01},
                                    settled_text="FIXED", budget_exceeded=False)
            if name == "global-review":
                return DriverResult(ok=True, reason="settled", stats={"cost": 0.01},
                                    settled_text=REVIEW, budget_exceeded=False)
            return DriverResult(ok=True, reason="settled", stats={"cost": 0.01},
                                settled_text="ok", budget_exceeded=False)

    result = run_paper(root, sections=["introduction"], driver_factory=_Driver,
                       budget=PaperBudget(max_fix_rounds=2))
    assert result.ok is False
    assert any("still failing re-score" in g for g in result.finish_gaps)
    assert any("re-score: FAIL" in v for v in result.issues_fixed_report.values())


def test_fix_confirmed_section_passes(tmp_path):
    from harness.paper_task import PaperBudget, run_paper

    root = tmp_path / "out"
    root.mkdir()
    write_checkpoint(root, {"topic": "T", "academic_level": "master",
                            "citation_style": "apa", "language": "en", "word_targets": {}})
    update_section_status(root, "introduction", status="written", passed=True,
                          updated_at="2026-01-01")
    (root / "drafts").mkdir()
    (root / "drafts" / "01_introduction.md").write_text("word " * 300, "utf-8")

    review = (
        "## GI-1 [high] scope: introduction\n"
        "Issue: weak framing.\n"
        "Suggested fix: strengthen the introduction framing.\n"
    )
    REVIEW = review
    calls = []

    class _Driver:
        def __init__(self, **kwargs):
            self.root = kwargs["root"]

        def run(self, prompt, name):
            from harness.driver import DriverResult
            calls.append(name)
            if name == "global-review":
                return DriverResult(ok=True, reason="settled", stats={"cost": 0.01},
                                    settled_text=REVIEW, budget_exceeded=False)
            return DriverResult(ok=True, reason="settled", stats={"cost": 0.01},
                                settled_text="FIXED", budget_exceeded=False)

    result = run_paper(root, sections=["introduction"], driver_factory=_Driver,
                       budget=PaperBudget())
    assert result.ok is True
    assert any("re-score: pass" in v for v in result.issues_fixed_report.values())
    assert calls == ["global-review", "fix-introduction"]  # section skipped (passed)


# -------------------------------------------------------- cost telemetry (driver)


def test_session_end_writes_spent_to_journal(tmp_path, monkeypatch):
    import threading

    from harness.driver import PiDriver

    scripted_events = [
        {"type": "turn_end", "message": {}, "toolResults": []},
        {"type": "agent_settled"},
    ]

    class FakeStdout:
        def __init__(self):
            self._items = []
            self._cv = threading.Condition()

        def feed(self, line):
            with self._cv:
                self._items.append(line)
                self._cv.notify()

        def __iter__(self):
            return self

        def __next__(self):
            with self._cv:
                while not self._items:
                    self._cv.wait()
                return self._items.pop(0)

    class FakeStdin:
        def __init__(self, stdout):
            self._stdout = stdout
            self._buf = ""
            self.closed = False

        def write(self, s):
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._dispatch(line)

        def _dispatch(self, line):
            cmd = json.loads(line)
            if cmd.get("type") == "prompt":
                for ev in scripted_events:
                    self._stdout.feed(json.dumps(ev) + "\n")
            elif cmd.get("type") == "get_session_stats":
                self._stdout.feed(json.dumps({
                    "type": "response", "id": cmd["id"], "command": "get_session_stats",
                    "success": True,
                    "data": {"cost": 0.07, "tokens": {"input": 3, "output": 2, "total": 5}},
                }) + "\n")
            elif cmd.get("type") == "get_last_assistant_text":
                self._stdout.feed(json.dumps({
                    "type": "response", "id": cmd["id"], "command": "get_last_assistant_text",
                    "success": True, "data": {"text": "FAKE FINAL TEXT"},
                }) + "\n")

        def flush(self):
            pass

        def close(self):
            self.closed = True

    class FakeProc:
        def __init__(self):
            self.stdout = FakeStdout()
            self.stdin = FakeStdin(self.stdout)
            self.stderr = iter([])
            self.terminated = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    import harness.driver as driver_mod
    monkeypatch.setattr(driver_mod.PiDriver, "_spawn", lambda self, argv, env: FakeProc())

    driver = PiDriver(tmp_path, pi_bin="fake-pi")
    result = driver.run("write something", name="test-spent")

    assert result.ok is True
    assert result.stats["cost"] == 0.07
    journal_text = (tmp_path / "run_journal.jsonl").read_text(encoding="utf-8")
    assert "session_end" in journal_text
    assert "spent=0.0700" in journal_text


def test_eval_reads_real_spent_marker(tmp_path):
    write_checkpoint(tmp_path, {"topic": "T"})
    (tmp_path / "run_journal.jsonl").write_text(
        json.dumps({"type": "session_end",
                    "summary": "session=section-results reason=settled spent=0.42"}) + "\n",
        encoding="utf-8",
    )
    metrics = evaluate_root(tmp_path)
    assert metrics.token_cost == pytest.approx(0.42)


def test_max_token_cost_threshold():
    m = EvalMetrics(quality_score=None, factcheck_clean=True, citation_rate=1.0,
                    token_cost=0.9, fix_rounds=0, forbidden_hits=0, cite_missing=0,
                    passed=True)
    fails = check_thresholds(m, {"max_token_cost": 0.5})
    assert any("token_cost" in f for f in fails)
    assert check_thresholds(m, {"max_token_cost": 1.0}) == []


# -------------------------------------------------- manage_claims resolve evidence


def test_manage_claims_resolve_requires_section_file(tmp_path):
    from agent_tools import registry

    spec = registry.get_tool("manage_claims")
    spec.func({"action": "record", "section": "introduction",
               "claims": [{"claim": "P equals NP"}]}, tmp_path)

    r = spec.func({"action": "resolve", "section": "introduction",
                   "claims": [{"claim": "P equals NP", "status": "deleted"}]}, tmp_path)

    assert r["ok"] is False
    assert r["is_retryable"] is True
    assert "not found" in r["error"]
