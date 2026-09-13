#!/usr/bin/env python3
"""Tests for checkpoint round-trip of the new structured-intent fields."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

from phases.context import DraftContext
from research_brief import ResearchBrief
from utils.checkpoint import load_checkpoint, restore_context, save_checkpoint


def _make_ctx(tmp_path):
    ctx = DraftContext(topic="T", verbose=False)
    ctx.folders = {"root": tmp_path, "drafts": tmp_path}
    return ctx


class TestBriefCheckpointRoundtrip:
    def test_save_and_restore_brief(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.research_brief = ResearchBrief(
            title="Paper X",
            research_questions=["RQ1: q"],
            baselines=["TGN"],
            metrics=[{"name": "MRR", "priority": "primary"}],
            forbidden_claims=["no causal claims"],
            venue_target="ICWSM",
            literature_search_questions=["q1"],
        )
        save_checkpoint(ctx, "research", tmp_path)

        _, completed = load_checkpoint(tmp_path / "checkpoint.json")
        assert completed == "research"

        restored = _make_ctx(tmp_path)
        restore_context(
            restored,
            json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8")),
        )
        assert restored.research_brief is not None
        assert restored.research_brief.title == "Paper X"
        assert restored.research_brief.baselines[0].name == "TGN"
        assert restored.research_brief.metrics[0].priority == "primary"
        assert restored.research_brief.forbidden_claims == ["no causal claims"]
        assert restored.research_brief.venue_target == "ICWSM"
        assert restored.research_brief.literature_search_questions == ["q1"]

    def test_save_custom_outline_fields(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.custom_outline = [{"title": "Intro", "role": "introduction"}]
        ctx.venue_target = "WWW"
        save_checkpoint(ctx, "structure", tmp_path)

        data = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
        assert data["custom_outline"][0]["title"] == "Intro"
        assert data["venue_target"] == "WWW"

        restored = _make_ctx(tmp_path)
        restore_context(restored, data)
        assert restored.venue_target == "WWW"

    def test_none_brief_ckpt_stays_none(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        save_checkpoint(ctx, "research", tmp_path)
        data = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
        assert data["research_brief"] is None

        restored = _make_ctx(tmp_path)
        restore_context(restored, data)
        assert restored.research_brief is None

    def test_legacy_checkpoint_still_loads(self, tmp_path):
        """Old checkpoints (no new fields) must restore without errors."""
        legacy = {
            "version": "1.0",
            "completed_phase": "compose",
            "topic": "Legacy",
            "blurb": None,
            "scout_output": "s",
            "architect_output": "a",
        }
        restored = _make_ctx(tmp_path)
        restore_context(restored, legacy)
        assert restored.topic == "Legacy"
        assert restored.research_brief is None
