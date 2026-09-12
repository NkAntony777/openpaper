#!/usr/bin/env python3
"""Contract tests for the agent tool layer (engine/agent_tools/).

Strategy follows docs/AGENT_HARNESS_DESIGN.md §11: schema/path validation,
guardrail triggers, idempotency, envelope error model. All network and LLM
dependencies are mocked — these tests never touch the network.
"""

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

from agent_tools import artifacts, claims, compile_export, literature, registry, revise, score, write_section
from agent_tools.common import write_checkpoint
from utils.citation_database import (
    Citation,
    add_citations_batch,
    create_empty_database,
    save_citation_database,
)


def make_citation(title, citation_id="", year=2020, authors=None, url=None):
    return Citation(
        citation_id=citation_id,
        authors=authors or ["Smith, J."],
        year=year,
        title=title,
        source_type="journal",
        url=url,
    )


def make_checkpoint(root: Path, **extra):
    data = {"topic": "Test Topic", "academic_level": "research_paper", "word_targets": {}}
    data.update(extra)
    write_checkpoint(root, data)
    return data


def section_content(words=200, cite=None):
    text = "word " * words
    if cite:
        text += f" {{{cite}}}."
    return text.strip()


class TestReadArtifact:
    def test_read_existing_file(self, tmp_path):
        (tmp_path / "research").mkdir()
        (tmp_path / "research" / "note.md").write_text("hello world", encoding="utf-8")
        result = artifacts.run({"path": "research/note.md"}, tmp_path)
        assert result["ok"] is True
        d = result["data"]
        assert d["content"] == "hello world"
        assert d["size"] == 11
        assert d["offset"] == 0
        assert d["truncated"] is False

    def test_read_missing_file(self, tmp_path):
        result = artifacts.run({"path": "research/nope.md"}, tmp_path)
        assert result["ok"] is False
        assert "artifact not found" in result["error"]

    def test_truncation_flag_and_size(self, tmp_path):
        big = "a" * 5000
        (tmp_path / "big.md").write_text(big, encoding="utf-8")
        r1 = artifacts.run({"path": "big.md"}, tmp_path)
        assert r1["ok"] and r1["data"]["truncated"] is True
        assert r1["data"]["size"] == 5000
        assert len(r1["data"]["content"]) == 4000  # DEFAULT_LIMIT
        r2 = artifacts.run({"path": "big.md", "offset": 4000}, tmp_path)
        assert r2["data"]["content"] == "a" * 1000
        assert r2["data"]["truncated"] is False
        assert r2["data"]["offset"] == 4000

    def test_limit_is_capped(self, tmp_path):
        (tmp_path / "big.md").write_text("b" * 50000, encoding="utf-8")
        r = artifacts.run({"path": "big.md", "limit": 10_000_000}, tmp_path)
        assert r["ok"] and len(r["data"]["content"]) == 20000  # MAX_LIMIT

    def test_relative_escape_rejected(self, tmp_path):
        result = artifacts.run({"path": "../outside.md"}, tmp_path)
        assert result["ok"] is False
        assert "escapes" in result["error"]

    def test_absolute_path_rejected(self, tmp_path):
        result = artifacts.run({"path": str(tmp_path / "x.md")}, tmp_path)
        assert result["ok"] is False
        assert "absolute" in result["error"]

    def test_binary_content_tolerated(self, tmp_path):
        (tmp_path / "bin.md").write_bytes(b"\xff\xfe\x00broken \x81 utf8")
        result = artifacts.run({"path": "bin.md"}, tmp_path)
        assert result["ok"] is True
        assert "\ufffd" in result["data"]["content"]

    def test_bad_offset_limit_type(self, tmp_path):
        (tmp_path / "f.md").write_text("x", encoding="utf-8")
        result = artifacts.run({"path": "f.md", "offset": "abc"}, tmp_path)
        assert result["ok"] is False
        assert "integers" in result["error"]


class TestWriteSection:
    def test_min_words_guardrail(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={"introduction": "600-800"})
        short = "this section is far too short"
        result = write_section.run({"section": "introduction", "content": short}, tmp_path)
        assert result["ok"] is False
        assert result["details"]["guardrail"] == "min_words"
        # floor = int(800 * 0.7)
        assert result["details"]["floor_words"] == 560
        assert result["details"]["target_words"] == 800
        assert result["details"]["actual_words"] == len(short.split())
        assert not (tmp_path / "drafts" / "01_introduction.md").exists()

    def test_unknown_citations_guardrail(self, tmp_path):
        bib = create_empty_database()
        add_citations_batch(bib, [make_citation("Known Paper", citation_id="cite_001")])
        save_citation_database(bib, tmp_path / "research" / "bibliography.json")
        content = section_content(cite="cite_999")
        result = write_section.run({"section": "conclusion", "content": content}, tmp_path)
        assert result["ok"] is False
        assert result["details"]["guardrail"] == "unknown_citations"
        assert result["details"]["unknown_citations"] == ["cite_999"]

    @pytest.mark.parametrize(
        "snippet,label",
        [
            ("TODO: finish this part", "TODO marker"),
            ("[INSERT supporting study here]", "[INSERT] placeholder"),
            ("[expand this paragraph]", "[expand] placeholder"),
            ("details are TBD", "TBD marker"),
            ("Lorem ipsum dolor sit amet", "Lorem ipsum filler"),
        ],
    )
    def test_placeholder_guardrails(self, tmp_path, snippet, label):
        content = f"{section_content(200)} {snippet}"
        result = write_section.run({"section": "conclusion", "content": content}, tmp_path)
        assert result["ok"] is False
        assert result["details"]["guardrail"] == "placeholder"
        assert result["details"]["placeholder"] == label

    def test_empty_content_rejected(self, tmp_path):
        result = write_section.run({"section": "conclusion", "content": "   "}, tmp_path)
        assert result["ok"] is False

    def test_idempotent_write_with_snapshot(self, tmp_path):
        content = section_content(100)
        args = {"section": "conclusion", "content": content}
        r1 = write_section.run(args, tmp_path)
        assert r1["ok"] is True
        assert r1["data"]["snapshot"] is None  # nothing to snapshot on first write
        r2 = write_section.run(args, tmp_path)
        assert r2["ok"] is True
        assert r2["data"]["snapshot"] is not None
        assert (tmp_path / r2["data"]["snapshot"]).exists()
        written = (tmp_path / "drafts" / "03_conclusion.md").read_text(encoding="utf-8")
        assert written == content

    def test_checkpoint_sync_intro(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={"introduction": "600-800"})
        content = section_content(600)
        result = write_section.run({"section": "introduction", "content": content}, tmp_path)
        assert result["ok"] is True
        assert result["data"]["checkpoint"] == "updated"
        ckpt = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
        assert ckpt["intro_output"] == content

    def test_checkpoint_sync_body_rebuild(self, tmp_path):
        make_checkpoint(
            tmp_path,
            word_targets={"literature_review": "100-200", "methodology": "100-200"},
        )
        lit = section_content(150)
        r = write_section.run({"section": "literature_review", "content": lit}, tmp_path)
        assert r["data"]["body"] is True
        ckpt = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
        assert ckpt["lit_review_output"] == lit
        assert ckpt["body_output"] == lit

        meth = section_content(150)
        r2 = write_section.run({"section": "methodology", "content": meth}, tmp_path)
        assert r2["data"]["body"] is True
        ckpt = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
        assert ckpt["methodology_output"] == meth
        assert ckpt["body_output"] == f"{lit}\n\n{meth}"

    def test_custom_section_slug_validation(self, tmp_path):
        bad = write_section.run(
            {"section": "custom", "slug": "Bad Slug!", "content": section_content(50)}, tmp_path
        )
        assert bad["ok"] is False
        assert "slug" in bad["error"]
        missing = write_section.run({"section": "custom", "content": section_content(50)}, tmp_path)
        assert missing["ok"] is False

    def test_custom_section_written_to_custom_dir(self, tmp_path):
        result = write_section.run(
            {"section": "custom", "slug": "related_work", "content": section_content(50)},
            tmp_path,
        )
        assert result["ok"] is True
        assert result["data"]["path"] == "drafts/custom_sections/custom_related_work.md"
        assert (tmp_path / "drafts" / "custom_sections" / "custom_related_work.md").exists()
        assert result["data"]["checkpoint"] == "skipped"

    def test_cite_missing_allowed_but_warned(self, tmp_path):
        content = f"{section_content(100)} {{cite_MISSING:evidence for this claim}}."
        result = write_section.run({"section": "conclusion", "content": content}, tmp_path)
        assert result["ok"] is True
        assert result["data"]["citations"] == []
        assert any("cite_MISSING" in w for w in result["data"]["warnings"])

    def test_undeclared_citation_warning(self, tmp_path):
        bib = create_empty_database()
        add_citations_batch(bib, [make_citation("Known Paper", citation_id="cite_001")])
        save_citation_database(bib, tmp_path / "research" / "bibliography.json")
        content = section_content(50, cite="cite_001")
        result = write_section.run(
            {"section": "conclusion", "content": content, "citations_used": ["cite_999"]},
            tmp_path,
        )
        assert result["ok"] is True
        assert any("cite_001" in w for w in result["data"]["warnings"])


class TestScoreDraft:
    def test_section_missing_file_fails(self, tmp_path):
        result = score.run({"scope": "section", "section": "introduction"}, tmp_path)
        assert result["ok"] is True
        assert result["data"]["passed"] is False
        assert any(i["metric"] == "present" for i in result["data"]["issues"])

    def test_section_word_floor_issue(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={"introduction": "600-800"})
        (tmp_path / "drafts").mkdir()
        (tmp_path / "drafts" / "01_introduction.md").write_text("too short", encoding="utf-8")
        result = score.run({"scope": "section", "section": "introduction"}, tmp_path)
        assert result["data"]["passed"] is False
        issue = next(i for i in result["data"]["issues"] if i["metric"] == "word_count")
        assert issue["severity"] == "warning"

    def test_section_placeholder_issue(self, tmp_path):
        (tmp_path / "drafts").mkdir()
        (tmp_path / "drafts" / "03_conclusion.md").write_text(
            section_content(100) + " TODO fix later", encoding="utf-8"
        )
        result = score.run({"scope": "section", "section": "conclusion"}, tmp_path)
        assert result["data"]["passed"] is False
        issue = next(i for i in result["data"]["issues"] if i["metric"] == "placeholder")
        assert issue["severity"] == "error"

    def test_section_healthy_passes(self, tmp_path):
        (tmp_path / "drafts").mkdir()
        (tmp_path / "drafts" / "03_conclusion.md").write_text(section_content(100), encoding="utf-8")
        result = score.run({"scope": "section", "section": "conclusion"}, tmp_path)
        assert result["data"]["passed"] is True
        assert result["data"]["issues"] == []

    def test_full_scope_structure(self, tmp_path):
        (tmp_path / "drafts").mkdir()
        texts = {
            "01_introduction.md": "# Intro\n\n" + section_content(200),
            "02_1_literature_review.md": "## Lit\n\n" + section_content(200),
            "02_2_methodology.md": "## Meth\n\n" + section_content(200),
            "02_3_analysis_results.md": "## Res\n\n" + section_content(200),
            "02_4_discussion.md": "## Disc\n\n" + section_content(200),
            "03_conclusion.md": "## Concl\n\n" + section_content(200),
        }
        for name, text in texts.items():
            (tmp_path / "drafts" / name).write_text(text, encoding="utf-8")
        result = score.run({"scope": "full"}, tmp_path)
        assert result["ok"] is True
        d = result["data"]
        assert d["scope"] == "full"
        assert isinstance(d["total"], int)
        assert set(d["breakdown"]) == {"word_count", "citations", "completeness", "structure"}
        for issue in d["issues"]:
            assert {"section", "metric", "severity"} <= set(issue)

    def test_full_scope_without_checkpoint_uses_defaults(self, tmp_path):
        result = score.run({"scope": "full"}, tmp_path)
        assert result["ok"] is True
        assert result["data"]["scope"] == "full"

    def test_unknown_section_rejected(self, tmp_path):
        result = score.run({"scope": "section", "section": "bogus"}, tmp_path)
        assert result["ok"] is False
        assert "unknown section" in result["error"]


class TestSearchLiterature:
    def _run_with_mocks(self, tmp_path, run_args=None, found=None,
                        research_side_effect=None, probe_error=None):
        researcher = mock.MagicMock()
        if research_side_effect is not None:
            researcher.research_citation.side_effect = research_side_effect
        else:
            researcher.research_citation.return_value = found or []
        args = {"query": "transformer networks"}
        args.update(run_args or {})
        with mock.patch.object(literature, "_probe_network", return_value=probe_error), \
             mock.patch.object(literature, "CitationResearcher", return_value=researcher):
            result = literature.run(args, tmp_path)
        return result, researcher

    def test_validation(self, tmp_path):
        assert literature.run({"query": "ab"}, tmp_path)["ok"] is False
        r = literature.run({"query": "valid query", "min_results": "abc"}, tmp_path)
        assert r["ok"] is False and "min_results" in r["error"]

    def test_min_results_zero_should_be_rejected(self, tmp_path):
        result, researcher = self._run_with_mocks(tmp_path, run_args={"min_results": 0}, found=[])
        assert result["ok"] is False
        researcher.research_citation.assert_not_called()

    def test_new_citations_appended_with_sequential_ids(self, tmp_path):
        found = [
            make_citation("Alpha Study", year=2019, url="http://a.example"),
            make_citation("Beta Study", year=2021, authors=["Doe, K."]),
        ]
        result, researcher = self._run_with_mocks(
            tmp_path, run_args={"min_results": 2}, found=found
        )
        assert result["ok"] is True
        d = result["data"]
        assert [c["id"] for c in d["new_citations"]] == ["cite_001", "cite_002"]
        assert d["total_in_db"] == 2
        assert d["meets_min_results"] is True
        researcher.research_citation.assert_called_once_with("transformer networks")

        bib = json.loads((tmp_path / "research" / "bibliography.json").read_text(encoding="utf-8"))
        assert [c["id"] for c in bib["citations"]] == ["cite_001", "cite_002"]
        summary = (tmp_path / "drafts" / "citation_summary.md").read_text(encoding="utf-8")
        assert "2 CITATIONS" in summary

    def test_ids_continue_after_existing_database(self, tmp_path):
        bib = create_empty_database()
        add_citations_batch(bib, [make_citation("Earlier Work", citation_id="cite_001")])
        save_citation_database(bib, tmp_path / "research" / "bibliography.json")

        result, _ = self._run_with_mocks(tmp_path, found=[make_citation("Fresh Work")])
        assert result["ok"] is True
        assert result["data"]["new_citations"][0]["id"] == "cite_002"
        assert result["data"]["total_in_db"] == 2

    def test_network_error_is_retryable(self, tmp_path):
        import requests
        result, _ = self._run_with_mocks(
            tmp_path, research_side_effect=requests.ConnectionError("boom")
        )
        assert result["ok"] is False
        assert result["is_retryable"] is True

    def test_probe_failure_is_retryable_and_skips_researcher(self, tmp_path):
        result, researcher = self._run_with_mocks(
            tmp_path, probe_error="network unreachable (ConnectTimeout)"
        )
        assert result["ok"] is False
        assert result["is_retryable"] is True
        researcher.research_citation.assert_not_called()

    def test_no_new_results_keeps_bibliography_intact(self, tmp_path):
        bib = create_empty_database()
        add_citations_batch(bib, [make_citation("Earlier Work", citation_id="cite_001")])
        save_citation_database(bib, tmp_path / "research" / "bibliography.json")
        before = (tmp_path / "research" / "bibliography.json").read_text(encoding="utf-8")

        result, _ = self._run_with_mocks(tmp_path, found=[make_citation("Earlier Work")])
        assert result["ok"] is True
        assert result["data"]["new_citations"] == []
        assert result["data"]["total_in_db"] == 1
        assert result["data"]["meets_min_results"] is False
        assert (tmp_path / "research" / "bibliography.json").read_text(encoding="utf-8") == before


class TestVerifyClaims:
    def _verdict(self, claim, verdict):
        return {
            "claim": claim,
            "verdict": verdict,
            "confidence": 0.9,
            "evidence_snippet": "evidence",
            "source_url": "http://x.example",
        }

    def _run_ok(self, tmp_path, verdicts, monkeypatch=None):
        fake_verifier = mock.MagicMock()
        fake_verifier.verify_claims.return_value = verdicts
        config = mock.MagicMock()
        config.google_api_key = "fake-key"
        with mock.patch("config.get_config", return_value=config), \
             mock.patch.object(claims, "_probe_network", return_value=None), \
             mock.patch("utils.agent_runner.setup_model", return_value=mock.MagicMock()), \
             mock.patch("utils.factcheck_verifier.FactCheckVerifier", return_value=fake_verifier):
            result = claims.run(
                {"claims": [{"claim": "the sky is blue", "section": "intro"}], "max_workers": 3},
                tmp_path,
            )
        return result, fake_verifier

    def test_validation(self, tmp_path):
        assert claims.run({}, tmp_path)["ok"] is False
        assert claims.run({"claims": []}, tmp_path)["ok"] is False
        r = claims.run({"claims": ["not a dict"]}, tmp_path)
        assert r["ok"] is False and "claims[0]" in r["error"]
        r = claims.run({"claims": [{"section": "intro"}]}, tmp_path)
        assert r["ok"] is False and "claim" in r["error"]
        r = claims.run({"claims": [{"claim": "  "}]}, tmp_path)
        assert r["ok"] is False

    def test_verdicts_pass_through_with_contradicted_count(self, tmp_path):
        verdicts = [
            self._verdict("a", "SUPPORTED"),
            self._verdict("b", "CONTRADICTED"),
            self._verdict("c", "SUPPORTED"),
        ]
        result, fake_verifier = self._run_ok(tmp_path, verdicts)
        assert result["ok"] is True
        assert result["data"]["verdicts"] == verdicts
        assert result["data"]["count"] == 3
        assert result["data"]["contradicted"] == 1
        assert result["data"]["find_replace"] == []
        _, kwargs = fake_verifier.verify_claims.call_args
        assert kwargs["max_workers"] == 3

    def test_contradicted_emits_find_replace_for_t4_t6(self, tmp_path):
        verdicts = [{
            "claim": "The model reaches 90% accuracy",
            "verdict": "CONTRADICTED",
            "confidence": 0.9,
            "wrong_part": "90%",
            "correct_value": "72%",
            "evidence_snippet": "reported 72%",
            "source_url": "http://x.example",
        }]
        result, _ = self._run_ok(tmp_path, verdicts)
        assert result["ok"] is True
        assert result["data"]["find_replace"] == [{
            "find": "90%", "replace": "72%",
            "claim": "The model reaches 90% accuracy",
        }]

    def test_missing_api_key_not_retryable(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        config = mock.MagicMock()
        config.google_api_key = ""
        with mock.patch("config.get_config", return_value=config):
            result = claims.run({"claims": [{"claim": "x"}]}, tmp_path)
        assert result["ok"] is False
        assert result["is_retryable"] is False
        assert result["details"]["missing_env"] == "GOOGLE_API_KEY"

    def test_network_failure_is_retryable(self, tmp_path):
        config = mock.MagicMock()
        config.google_api_key = "fake-key"
        with mock.patch("config.get_config", return_value=config), \
             mock.patch.object(claims, "_probe_network", return_value="network unreachable"):
            result = claims.run({"claims": [{"claim": "x"}]}, tmp_path)
        assert result["ok"] is False
        assert result["is_retryable"] is True


class TestReviseSection:
    INTRO = "drafts/01_introduction.md"

    def _write_intro(self, tmp_path, text):
        (tmp_path / "drafts").mkdir(exist_ok=True)
        (tmp_path / self.INTRO).write_text(text, encoding="utf-8")

    def test_missing_file_fails(self, tmp_path):
        result = revise.run(
            {"section": "introduction", "instructions": "improve"}, tmp_path
        )
        assert result["ok"] is False
        assert "has no file yet" in result["error"]
        assert result["is_retryable"] is False

    def test_unknown_section_fails(self, tmp_path):
        result = revise.run({"section": "nope", "instructions": "improve"}, tmp_path)
        assert result["ok"] is False

    def test_unique_find_replace_skips_llm(self, tmp_path):
        self._write_intro(tmp_path, "The model reaches 90% accuracy on the benchmark.")
        with mock.patch.object(revise, "call_gemini_revise") as llm:
            result = revise.run(
                {
                    "section": "introduction",
                    "instructions": "update the number",
                    "find_replace": [{"find": "90%", "replace": "91%"}],
                },
                tmp_path,
            )
        assert result["ok"] is True
        assert result["data"]["applied_find_replace"] is True
        assert result["data"]["llm_revised"] is False
        assert result["data"]["missed_replacements"] == []
        llm.assert_not_called()
        assert "91%" in (tmp_path / self.INTRO).read_text(encoding="utf-8")

    def test_ambiguous_find_goes_to_llm_with_pairs_in_instructions(self, tmp_path):
        original = "A good result and another good outcome."
        self._write_intro(tmp_path, original)
        llm_text = "A fine result and another fine outcome."
        with mock.patch.object(revise, "call_gemini_revise", return_value=llm_text) as llm:
            result = revise.run(
                {
                    "section": "introduction",
                    "instructions": "use finer words",
                    "find_replace": [{"find": "good", "replace": "fine"}],
                },
                tmp_path,
            )
        assert result["ok"] is True
        assert result["data"]["applied_find_replace"] is False
        assert result["data"]["llm_revised"] is True
        missed = result["data"]["missed_replacements"]
        assert missed and missed[0]["find"] == "good"
        assert "ambiguous" in missed[0]["reason"]
        instructions = llm.call_args.args[1]
        assert 'Find: "good"' in instructions
        assert 'Replace with: "fine"' in instructions
        assert "finer words" in instructions
        assert (tmp_path / self.INTRO).read_text(encoding="utf-8") == llm_text

    def test_guardrail_rejects_llm_result_and_keeps_file(self, tmp_path):
        original = section_content(50)
        self._write_intro(tmp_path, original)
        with mock.patch.object(
            revise, "call_gemini_revise", return_value=section_content(50) + " TODO refine"
        ):
            result = revise.run(
                {"section": "introduction", "instructions": "polish"},
                tmp_path,
            )
        assert result["ok"] is False
        assert result["details"]["guardrail"] == "placeholder"
        assert (tmp_path / self.INTRO).read_text(encoding="utf-8") == original

    def test_llm_failure_retryable_classification(self, tmp_path):
        self._write_intro(tmp_path, section_content(50))
        with mock.patch.object(
            revise, "call_gemini_revise", side_effect=RuntimeError("429 rate limit")
        ):
            result = revise.run(
                {"section": "introduction", "instructions": "polish"}, tmp_path
            )
        assert result["ok"] is False
        assert result["is_retryable"] is True

    def test_checkpoint_synced_after_direct_replace(self, tmp_path):
        make_checkpoint(tmp_path)
        original = "The dataset contains 1000 samples."
        self._write_intro(tmp_path, original)
        result = revise.run(
            {
                "section": "introduction",
                "instructions": "update count",
                "find_replace": [{"find": "1000", "replace": "2000"}],
            },
            tmp_path,
        )
        assert result["ok"] is True
        ckpt = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
        assert ckpt["intro_output"] == "The dataset contains 2000 samples."


class TestCompileDraft:
    def _make_root(self, tmp_path):
        research = tmp_path / "research"
        research.mkdir()
        bib = create_empty_database()
        add_citations_batch(bib, [make_citation("Known Paper", citation_id="cite_001")])
        save_citation_database(bib, research / "bibliography.json")
        write_checkpoint(
            tmp_path,
            {
                "topic": "T",
                "completed_phase": "compose",
                "folders": {
                    "research": str(research),
                    "exports": str(tmp_path / "exports"),
                },
            },
        )

    def _fake_export(self, ctx):
        exports = Path(ctx.folders["exports"])
        exports.mkdir(parents=True, exist_ok=True)
        pdf = exports / "paper_test.pdf"
        docx = exports / "paper_test.docx"
        md = exports / "paper_test.md"
        for p in (pdf, docx, md):
            p.write_text("fake", encoding="utf-8")
        return str(pdf), str(docx)

    def _run(self, tmp_path, fmt="all", export_impl=None):
        with mock.patch("config.get_config", return_value=mock.MagicMock()), \
             mock.patch("utils.agent_runner.setup_model", return_value=mock.MagicMock()), \
             mock.patch(
                 "phases.compile.run_compile_and_export",
                 side_effect=export_impl or self._fake_export,
             ) as run_compile:
            result = compile_export.run({"format": fmt}, tmp_path)
        return result, run_compile

    def test_missing_checkpoint_fails(self, tmp_path):
        result = compile_export.run({"format": "all"}, tmp_path)
        assert result["ok"] is False
        assert result["is_retryable"] is False
        assert "checkpoint.json" in result["error"]

    def test_compile_exception_not_retryable(self, tmp_path):
        self._make_root(tmp_path)
        boom = RuntimeError("pandoc exploded")
        result, _ = self._run(tmp_path, export_impl=mock.Mock(side_effect=boom))
        assert result["ok"] is False
        assert result["is_retryable"] is False
        assert "pandoc exploded" in result["error"]

    def test_success_reports_existing_exports(self, tmp_path):
        self._make_root(tmp_path)
        result, _ = self._run(tmp_path, fmt="all")
        assert result["ok"] is True
        exports = result["data"]["exports"]
        assert set(exports) == {"md", "pdf", "docx"}
        assert exports["pdf"] == "exports/paper_test.pdf"
        assert result["data"]["completed_phase"] == "compose"

    def test_format_filter(self, tmp_path):
        self._make_root(tmp_path)
        result, _ = self._run(tmp_path, fmt="pdf")
        assert result["ok"] is True
        assert set(result["data"]["exports"]) == {"pdf"}

    def test_unknown_format_rejected(self, tmp_path):
        result = compile_export.run({"format": "epub"}, tmp_path)
        assert result["ok"] is False


class TestRegistry:
    def test_list_tools_returns_all_without_errors(self):
        available, errors = registry.list_tools()
        assert errors == []
        assert sorted(t["name"] for t in available) == [
            "compile_draft",
            "manage_claims",
            "read_artifact",
            "revise_section",
            "score_draft",
            "search_literature",
            "verify_claims",
            "write_outline",
            "write_section",
        ]

    def test_get_tool_unknown_raises_keyerror(self):
        with pytest.raises(KeyError):
            registry.get_tool("does_not_exist")


class TestCliEnvelope:
    """Envelope contract of `opendraft tool` — run_tool_command called in-process."""

    def _run_cli(self, capsys, argv):
        from opendraft.cli import run_tool_command
        code = run_tool_command(argv)
        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert len(lines) == 1, f"expected single JSON line, got: {out!r}"
        return code, json.loads(lines[0])

    def test_list_command(self, capsys, tmp_path):
        code, payload = self._run_cli(capsys, ["list"])
        assert code == 0
        assert payload["ok"] is True
        assert len(payload["data"]["tools"]) == 9
        assert payload["data"]["errors"] == []

    def test_tool_success_exit_zero(self, capsys, tmp_path):
        (tmp_path / "note.md").write_text("hello", encoding="utf-8")
        code, payload = self._run_cli(
            capsys,
            ["read_artifact", "--root", str(tmp_path), "--args", '{"path": "note.md"}'],
        )
        assert code == 0
        assert payload["ok"] is True
        assert payload["data"]["content"] == "hello"

    def test_tool_failure_exit_one(self, capsys, tmp_path):
        code, payload = self._run_cli(
            capsys,
            ["read_artifact", "--root", str(tmp_path), "--args", '{"path": "nope.md"}'],
        )
        assert code == 1
        assert payload["ok"] is False
        assert "artifact not found" in payload["error"]

    def test_unknown_tool_exit_two(self, capsys, tmp_path):
        code, payload = self._run_cli(capsys, ["nope_tool"])
        assert code == 2
        assert payload["ok"] is False

    def test_invalid_args_json_exit_two(self, capsys, tmp_path):
        code, payload = self._run_cli(
            capsys, ["read_artifact", "--root", str(tmp_path), "--args", "{not json"]
        )
        assert code == 2
        assert payload["ok"] is False

    def test_tool_crash_becomes_envelope(self, capsys, tmp_path):
        """Unexpected exceptions inside a tool are converted, not propagated."""
        from agent_tools import registry
        spec = registry.get_tool("read_artifact")
        with mock.patch.object(spec, "func", side_effect=RuntimeError("unexpected boom")):
            code, payload = self._run_cli(
                capsys,
                ["read_artifact", "--root", str(tmp_path), "--args", '{"path": "x.md"}'],
            )
        assert code == 1
        assert payload["ok"] is False
        assert "unexpected boom" in payload["error"]


class TestStatusLedger:
    """section_status.json — the paper-level state ledger (M2 non-linear oversight)."""

    def _bib(self, tmp_path):
        bib = create_empty_database()
        add_citations_batch(bib, [make_citation("Known Paper", citation_id="cite_001")])
        save_citation_database(bib, tmp_path / "research" / "bibliography.json")

    def test_write_section_updates_ledger(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={})
        self._bib(tmp_path)
        content = section_content(200, cite="cite_001")
        result = write_section.run(
            {"section": "introduction", "content": content, "summary": "Claims X."}, tmp_path
        )
        assert result["ok"] is True
        ledger = result["data"]["status_ledger"]
        assert ledger["status"] == "written"
        assert ledger["words"] == len(content.split())
        assert ledger["citations_count"] == 1
        assert "updated_at" in ledger

        on_disk = json.loads((tmp_path / "section_status.json").read_text(encoding="utf-8"))
        assert on_disk["sections"]["introduction"]["status"] == "written"

    def test_revise_updates_ledger_status(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={})
        content = "The ALPHA framework improves retention. " + section_content(100)
        write_section.run({"section": "conclusion", "content": content}, tmp_path)
        with mock.patch.object(revise, "call_gemini_revise") as llm:
            result = revise.run(
                {
                    "section": "conclusion",
                    "instructions": "update the name",
                    "find_replace": [{"find": "ALPHA framework", "replace": "ALPHA-2 framework"}],
                },
                tmp_path,
            )
        llm.assert_not_called()
        assert result["ok"] is True
        assert result["data"]["status_ledger"]["status"] == "revised"
        assert result["data"]["status_ledger"]["words"] == len(
            (tmp_path / "drafts" / "03_conclusion.md").read_text(encoding="utf-8").split()
        )

    def test_score_section_ledger_records_failures(self, tmp_path):
        # empty/missing section: error severity lands in the ledger
        make_checkpoint(tmp_path, word_targets={})
        result = score.run({"scope": "section", "section": "introduction"}, tmp_path)
        assert result["ok"] is True
        ledger = result["data"]["status_ledger"]
        assert ledger["passed"] is False
        assert len(ledger["open_issues"]) == 1
        assert "empty or missing" in ledger["open_issues"][0]

    def test_score_section_ledger_merges_with_write_entry(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={})
        content = section_content(100)
        write_section.run({"section": "introduction", "content": content}, tmp_path)

        # raise the bar after the write: score now reports the word_count warning
        make_checkpoint(tmp_path, word_targets={"introduction": "2000-2500"})
        result = score.run({"scope": "section", "section": "introduction"}, tmp_path)
        assert result["ok"] is True
        ledger = result["data"]["status_ledger"]
        assert ledger["passed"] is False
        assert any("section short" in i for i in ledger["open_issues"])
        # merge update: the write_section fields survive scoring
        assert ledger["status"] == "written"
        assert ledger["words"] == len(content.split())

    def test_score_section_ledger_records_pass(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={})
        write_section.run({"section": "conclusion", "content": section_content(100)}, tmp_path)
        result = score.run({"scope": "section", "section": "conclusion"}, tmp_path)
        assert result["data"]["status_ledger"]["passed"] is True
        assert result["data"]["status_ledger"]["open_issues"] == []

    def test_score_full_updates_full_entry(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={})
        write_section.run({"section": "introduction", "content": section_content(100)}, tmp_path)
        write_section.run({"section": "conclusion", "content": section_content(100)}, tmp_path)

        result = score.run({"scope": "full"}, tmp_path)
        assert result["ok"] is True
        ledger = result["data"]["status_ledger"]
        assert isinstance(ledger["last_total"], int)
        assert isinstance(ledger["last_passed"], bool)
        assert isinstance(ledger["open_issues"], list)
        on_disk = json.loads((tmp_path / "section_status.json").read_text(encoding="utf-8"))
        assert on_disk["full"]["last_total"] == ledger["last_total"]
        # section entries live in their own bucket
        assert "last_total" not in on_disk["sections"]["introduction"]

    def test_merge_preserves_unknown_fields(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={})
        write_section.run({"section": "conclusion", "content": section_content(50)}, tmp_path)
        p = tmp_path / "section_status.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["sections"]["conclusion"]["reviewed_by"] = "human"
        p.write_text(json.dumps(data), encoding="utf-8")

        write_section.run({"section": "conclusion", "content": section_content(80)}, tmp_path)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["sections"]["conclusion"]["reviewed_by"] == "human"
        assert data["sections"]["conclusion"]["status"] == "written"

    def test_custom_section_skips_status_ledger(self, tmp_path):
        result = write_section.run(
            {"section": "custom", "slug": "extra_bits", "content": section_content(50),
             "summary": "Extra material."},
            tmp_path,
        )
        assert result["ok"] is True
        assert result["data"]["status_ledger"] is None
        assert not (tmp_path / "section_status.json").exists()
        assert result["data"]["summary_ledger"] == "drafts/.ledger/custom_extra_bits.md"
        assert (tmp_path / "drafts" / ".ledger" / "custom_extra_bits.md").exists()


class TestSectionSummaryLedger:
    """drafts/.ledger/*.md — low-token summaries feeding the global review pass."""

    def test_summary_written_to_ledger(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={})
        result = write_section.run(
            {"section": "literature_review", "content": section_content(100),
             "summary": "Reviews prior work on X; key terms: a, b."},
            tmp_path,
        )
        assert result["ok"] is True
        rel = result["data"]["summary_ledger"]
        assert rel == "drafts/.ledger/literature_review.summary.md"
        assert (tmp_path / rel).read_text(encoding="utf-8") == "Reviews prior work on X; key terms: a, b."

    def test_overlong_summary_truncated_with_warning(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={})
        result = write_section.run(
            {"section": "conclusion", "content": section_content(50), "summary": "x" * 700},
            tmp_path,
        )
        assert result["ok"] is True
        assert any("truncated" in w for w in result["data"]["warnings"])
        written = (tmp_path / "drafts" / ".ledger" / "conclusion.summary.md").read_text(
            encoding="utf-8"
        )
        assert len(written) == 600

    def test_summary_absent_leaves_no_ledger_file(self, tmp_path):
        make_checkpoint(tmp_path, word_targets={})
        result = write_section.run(
            {"section": "conclusion", "content": section_content(50)}, tmp_path
        )
        assert result["ok"] is True
        assert result["data"]["summary_ledger"] is None
        assert not (tmp_path / "drafts" / ".ledger").exists()

    def test_summary_field_in_python_and_ts_schema(self, tmp_path):
        props = write_section.INPUT_SCHEMA["properties"]
        assert "summary" in props
        ts_path = (
            Path(__file__).parent.parent / "engine" / "harness" / "assets" / "opendraft-tools.ts"
        )
        ts = ts_path.read_text(encoding="utf-8")
        assert "summary: Type.Optional(" in ts
        assert "global review" in ts
