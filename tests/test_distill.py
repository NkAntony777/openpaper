#!/usr/bin/env python3
"""Offline tests for M3/M4 additions: journal_distill rules + CLI, paper_map lessons block,
and contract tests for the two new agent tools (write_outline, manage_claims)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

from agent_tools import registry
from agent_tools.common import update_section_status, write_checkpoint
from harness.journal_distill import distill
from harness.paper_map import write_paper_map


# ----------------------------------------------------------------- journal fixtures


def _journal(root: Path, entries):
    lines = [json.dumps({"ts": "2026-01-01T00:00:00+00:00", "type": t, "summary": s})
             for t, s in entries]
    (root / "run_journal.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_start(section):
    return ("tool_execution_start",
            f'write_section {{"section": "{section}", "content": "x"}}')


def _write_end(error=False):
    return ("tool_execution_end", "write_section ERROR" if error else "write_section ok")


# ------------------------------------------------------------- rule 1: min_words


def test_rule_min_words_rejections(tmp_path):
    _journal(tmp_path, [
        _write_start("methodology"), _write_end(error=True),
        _write_start("methodology"), _write_end(error=True),
        _write_start("methodology"), _write_end(error=False),
    ])

    summary = distill(tmp_path)

    lessons = [l for l in summary["lessons"] if "methodology" in l["lesson"]]
    assert len(lessons) == 1
    assert "aim for >=90% of the word target" in lessons[0]["lesson"] or \
           "rejected" in lessons[0]["lesson"]
    assert lessons[0]["confidence"] >= 0.6
    assert "2 write_section ERROR" in lessons[0]["evidence"]
    # marker text in the journal upgrades specificity/confidence
    assert (tmp_path / "lessons_proposed.md").exists()
    assert "HUMAN REVIEW" in (tmp_path / "lessons_proposed.md").read_text(encoding="utf-8")


def test_rule_min_words_specific_when_marker_present(tmp_path):
    _journal(tmp_path, [
        _write_start("results"), _write_end(error=True),
        ("extension_error", "tool_call: write_section failed: guardrail: min_words"),
        _write_start("results"), _write_end(error=True),
    ])

    summary = distill(tmp_path)

    lessons = [l for l in summary["lessons"]
               if "results" in l["lesson"] and "word target" in l["lesson"]]
    assert len(lessons) == 1
    assert lessons[0]["confidence"] == 0.9


def test_rule_min_words_below_threshold_quiet(tmp_path):
    _journal(tmp_path, [_write_start("results"), _write_end(error=True)])
    summary = distill(tmp_path)
    assert summary["lessons"] == []


# ------------------------------------------------------------- rule 2: tool streak


def test_rule_tool_unreliable_streak(tmp_path):
    _journal(tmp_path, [
        ("tool_execution_start", 'search_literature {"query": "ai"}'),
        ("tool_execution_end", "search_literature ERROR"),
        ("tool_execution_start", 'search_literature {"query": "ai edu"}'),
        ("tool_execution_end", "search_literature ERROR"),
        ("tool_execution_start", 'search_literature {"query": "education"}'),
        ("tool_execution_end", "search_literature ERROR"),
    ])

    summary = distill(tmp_path)

    lessons = [l for l in summary["lessons"] if "search_literature" in l["lesson"]]
    assert len(lessons) == 1
    assert "unreliable" in lessons[0]["lesson"]
    assert "alternative" in lessons[0]["lesson"]


def test_rule_tool_streak_resets_on_ok(tmp_path):
    _journal(tmp_path, [
        ("tool_execution_end", "verify_claims ERROR"),
        ("tool_execution_end", "verify_claims ERROR"),
        ("tool_execution_end", "verify_claims ok"),
        ("tool_execution_end", "verify_claims ERROR"),
    ])
    summary = distill(tmp_path)
    assert [l for l in summary["lessons"] if "verify_claims" in l["lesson"]] == []


# ---------------------------------------------------- rule 3: metric persistence


def test_rule_metric_persists_across_rescores(tmp_path):
    _journal(tmp_path, [
        ("tool_execution_start", 'score_draft {"scope": "section", "section": "introduction"}'),
        ("tool_execution_end", "score_draft ok"),
        ("tool_execution_start", 'score_draft {"scope": "section", "section": "introduction"}'),
        ("tool_execution_end", "score_draft ok"),
    ])
    update_section_status(tmp_path, "introduction", status="written", passed=False,
                          open_issues=["section short: 40 words (floor 56, target 80)"],
                          updated_at="2026-01-01T00:00:00")

    summary = distill(tmp_path)

    lessons = [l for l in summary["lessons"] if "persists" in l["lesson"]]
    assert len(lessons) == 1
    assert "metric word_count persists for section introduction" in lessons[0]["lesson"]
    assert "search_literature" in lessons[0]["lesson"]


def test_rule_metric_no_rescore_no_lesson(tmp_path):
    _journal(tmp_path, [
        ("tool_execution_start", 'score_draft {"scope": "section", "section": "introduction"}'),
        ("tool_execution_end", "score_draft ok"),
    ])
    update_section_status(tmp_path, "introduction", status="written", passed=False,
                          open_issues=["section short: 40 words"], updated_at="2026-01-01")
    summary = distill(tmp_path)
    assert [l for l in summary["lessons"] if "persists" in l["lesson"]] == []


# ------------------------------------------------------------- rule 4: fix rounds


def test_rule_fix_rounds(tmp_path):
    _journal(tmp_path, [
        ("prompt", "session=section-results model=m budget(cost=0.3)"),
        ("prompt", "session=fix-results model=m budget(cost=0.15)"),
        ("prompt", "session=fix-results model=m budget(cost=0.15)"),
        ("prompt", "session=fix-results model=m budget(cost=0.15)"),
    ])

    summary = distill(tmp_path)

    lessons = [l for l in summary["lessons"] if "fix rounds" in l["lesson"]]
    assert len(lessons) == 1
    assert "results needed 3 fix rounds" in lessons[0]["lesson"]
    assert "richer initial context" in lessons[0]["lesson"]


def test_rule_fix_rounds_single_round_quiet(tmp_path):
    _journal(tmp_path, [("prompt", "session=fix-discussion model=m")])
    summary = distill(tmp_path)
    assert summary["lessons"] == []


# ------------------------------------------------------------------ CLI: distill


def test_cli_harness_distill_envelope(tmp_path, capsys):
    _journal(tmp_path, [
        _write_start("methodology"), _write_end(error=True),
        _write_start("methodology"), _write_end(error=True),
    ])
    from opendraft.cli import run_harness_command

    rc = run_harness_command(["distill", "--root", str(tmp_path)])

    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["data"]["proposed"] == 1
    assert payload["data"]["lessons_path"] == str(tmp_path / "lessons_proposed.md")
    assert "[harness]" in captured.err
    assert Path(payload["data"]["lessons_path"]).exists()


def test_cli_harness_distill_empty_journal(tmp_path, capsys):
    from opendraft.cli import run_harness_command

    rc = run_harness_command(["distill", "--root", str(tmp_path)])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["data"]["proposed"] == 0


# -------------------------------------------------------- paper_map lessons block


def test_paper_map_lessons_block_renders_approved(tmp_path):
    write_checkpoint(tmp_path, {"topic": "T", "word_targets": {}})
    approved = tmp_path / "lessons" / "approved"
    approved.mkdir(parents=True)
    long_text = "lesson body " * 40  # > 300 chars
    (approved / "short_drafts.md").write_text(long_text.strip(), "utf-8")

    write_paper_map(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    assert "## Lessons learned" in text
    assert "- short_drafts: " in text
    snippet = text.split("- short_drafts: ")[1].splitlines()[0]
    assert len(snippet) <= 300
    assert "drafts/.ledger/*.claims.jsonl" in text  # M3a Material hint


def test_paper_map_lessons_block_empty(tmp_path):
    write_checkpoint(tmp_path, {"topic": "T", "word_targets": {}})
    write_paper_map(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "## Lessons learned" in text
    assert "(none yet" in text


# ------------------------------------------------- new tools: contract smoke tests


def test_write_outline_overwrite_and_checkpoint_sync(tmp_path):
    write_checkpoint(tmp_path, {"topic": "T", "formatter_output": "OLD"})
    spec = registry.get_tool("write_outline")

    r = spec.func({"content": "# Outline\n\n## A\nalpha\n\n## B\nbeta"}, tmp_path)
    assert r["ok"] is True
    assert r["data"]["path"] == "drafts/00_formatted_outline.md"
    assert r["data"]["checkpoint"] == "updated"
    ckpt = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert ckpt["formatter_output"].startswith("# Outline")


def test_write_outline_merge_replaces_matching_headings_only(tmp_path):
    spec = registry.get_tool("write_outline")
    spec.func({"content": "# Outline\n\n## A\nalpha\n\n## B\nbeta"}, tmp_path)

    r = spec.func({"content": "## A\nalpha2", "merge": True}, tmp_path)

    assert r["ok"] is True
    assert r["data"]["replaced_blocks"] == 1
    text = (tmp_path / "drafts" / "00_formatted_outline.md").read_text(encoding="utf-8")
    assert "alpha2" in text and "## B\nbeta" in text


def test_manage_claims_record_list_and_id_increment(tmp_path):
    spec = registry.get_tool("manage_claims")

    r = spec.func({"action": "record", "section": "methodology",
                   "claims": [{"claim": "AI helps", "line": "l1"}, {"claim": "42% gain"}]},
                  tmp_path)
    assert r["ok"] and r["data"]["recorded"] == 2
    assert r["data"]["ids"] == ["CL-METHODOLOGY-1", "CL-METHODOLOGY-2"]

    r = spec.func({"action": "record", "section": "methodology",
                   "claims": [{"claim": "third"}]}, tmp_path)
    assert r["data"]["ids"] == ["CL-METHODOLOGY-3"]

    r = spec.func({"action": "list", "section": "methodology"}, tmp_path)
    assert r["data"]["count"] == 3
    ledger = tmp_path / "drafts" / ".ledger" / "methodology.claims.jsonl"
    assert ledger.exists()


def test_manage_claims_verify_needs_key(tmp_path, monkeypatch):
    import types
    monkeypatch.setattr("config.get_config",
                        lambda: types.SimpleNamespace(google_api_key=""), raising=False)
    spec = registry.get_tool("manage_claims")
    spec.func({"action": "record", "section": "results",
               "claims": [{"claim": "x"}]}, tmp_path)

    r = spec.func({"action": "verify", "section": "results"}, tmp_path)

    assert r["ok"] is False
    assert r["is_retryable"] is False
    assert "GOOGLE_API_KEY" in r["error"]


def test_manage_claims_rejects_bad_action_and_section(tmp_path):
    spec = registry.get_tool("manage_claims")
    assert spec.func({"action": "nope", "section": "results"}, tmp_path)["ok"] is False
    assert spec.func({"action": "list", "section": "nope"}, tmp_path)["ok"] is False


def _write_intro(tmp_path, text):
    (tmp_path / "drafts").mkdir(exist_ok=True)
    (tmp_path / "drafts" / "01_introduction.md").write_text(text, encoding="utf-8")


def test_manage_claims_resolve_revised_requires_wrong_part_gone(tmp_path):
    spec = registry.get_tool("manage_claims")
    spec.func({"action": "record", "section": "introduction",
               "claims": [{"claim": "The model reaches 99% accuracy"}]}, tmp_path)
    ledger = tmp_path / "drafts" / ".ledger" / "introduction.claims.jsonl"
    entry = json.loads(ledger.read_text(encoding="utf-8").strip())
    entry["verdict"] = {"verdict": "CONTRADICTED", "wrong_part": "99%", "correct_value": "72%"}
    ledger.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    _write_intro(tmp_path, "The model reaches 99% accuracy on the split.")

    blocked = spec.func({"action": "resolve", "section": "introduction",
                         "claims": [{"id": "CL-INTRODUCTION-1", "status": "revised"}]},
                        tmp_path)
    assert blocked["ok"] is False
    assert blocked["is_retryable"] is True

    _write_intro(tmp_path, "The model reaches 72% accuracy on the split.")
    ok_r = spec.func({"action": "resolve", "section": "introduction",
                      "claims": [{"id": "CL-INTRODUCTION-1", "status": "revised",
                                  "note": "applied find_replace"}]}, tmp_path)
    assert ok_r["ok"] is True
    assert ok_r["data"]["ids"] == ["CL-INTRODUCTION-1"]
    assert ok_r["data"]["unresolved_contradicted"] == 0
    stored = json.loads(ledger.read_text(encoding="utf-8").strip())
    assert stored["resolution"]["status"] == "revised"


def test_manage_claims_resolve_deleted_requires_claim_gone(tmp_path):
    spec = registry.get_tool("manage_claims")
    spec.func({"action": "record", "section": "introduction",
               "claims": [{"claim": "P equals NP"}]}, tmp_path)
    _write_intro(tmp_path, "We do not discuss P equals NP in this draft.")
    blocked = spec.func({"action": "resolve", "section": "introduction",
                         "claims": [{"claim": "P equals NP", "status": "deleted"}]},
                        tmp_path)
    assert blocked["ok"] is False

    _write_intro(tmp_path, "Complexity claims are out of scope.")
    ok_r = spec.func({"action": "resolve", "section": "introduction",
                      "claims": [{"claim": "P equals NP", "status": "deleted"}]},
                     tmp_path)
    assert ok_r["ok"] is True
    assert ok_r["data"]["resolved"] == 1

