#!/usr/bin/env python3
"""Regression tests for the compile citation-order fix (docs/AGENT_HARNESS_DESIGN.md §4 T7).

CitationCompiler.generate_reference_list_for_ids must include citations that were
researched AFTER the source text was fixed (e.g. {cite_MISSING} auto-research adds
new ids during compile_citations, so those ids never appear in the original text).
The legacy entry point generate_reference_list(text) must keep its old behavior:
delegate to generate_reference_list_for_ids over the ids extracted from the text.
"""

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "engine"))

from utils.citation_compiler import CitationCompiler
from utils.citation_database import Citation, CitationDatabase


def make_db():
    db = CitationDatabase(
        citations=[
            Citation(
                citation_id="cite_001",
                authors=["Smith, J."],
                year=2019,
                title="Foundations of the Field",
                source_type="journal",
            ),
            Citation(
                citation_id="cite_002",
                authors=["Doe, K."],
                year=2021,
                title="Late Breaking Result",
                source_type="journal",
            ),
        ],
        citation_style="APA 7th",
        draft_language="english",
    )
    return db


def make_compiler(db):
    # CitationCompiler.__init__ instantiates a real CitationResearcher — keep it offline.
    with mock.patch("utils.citation_compiler.CitationResearcher"):
        return CitationCompiler(database=db, model=None)


class TestGenerateReferenceListForIds:
    def test_includes_ids_beyond_text_tokens(self):
        """The compile fix: ids researched after the text was fixed must appear."""
        compiler = make_compiler(make_db())
        text = "Some argument {cite_001}."
        # cite_002 was added to the database by {cite_MISSING} auto-research and
        # is therefore not present as a {cite_XXX} token in the source text.
        refs = compiler.generate_reference_list_for_ids({"cite_001", "cite_002"}, text=text)
        assert "Foundations of the Field" in refs
        assert "Late Breaking Result" in refs

    def test_legacy_entry_point_delegates_and_stays_text_scoped(self):
        """Old behavior unchanged: generate_reference_list(text) only covers text ids."""
        compiler = make_compiler(make_db())
        text = "Some argument {cite_001}."
        legacy = compiler.generate_reference_list(text)
        assert "Foundations of the Field" in legacy
        assert "Late Breaking Result" not in legacy
        # Delegation: same result as calling the new method with the extracted ids.
        assert legacy == compiler.generate_reference_list_for_ids({"cite_001"}, text=text)

    def test_header_present_for_new_ids(self):
        compiler = make_compiler(make_db())
        refs = compiler.generate_reference_list_for_ids({"cite_002"})
        assert "References" in refs
        assert "Late Breaking Result" in refs
