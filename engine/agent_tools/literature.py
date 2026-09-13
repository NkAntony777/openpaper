#!/usr/bin/env python3
"""
ABOUTME: T3 search_literature — semantic literature search. Thin wrapper over the
ABOUTME: multi-provider CitationResearcher (Crossref/OpenAlex/Semantic Scholar/Gemini Grounded).
ABOUTME: New citations are deduplicated into research/bibliography.json (cite_XXX ids)
ABOUTME: and drafts/citation_summary.md is rebuilt, so found sources are citable by
ABOUTME: write_section immediately. Never invents citations (LLM fallback stays disabled).
"""

import contextlib
import io
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

import requests
from agent_tools import registry
from agent_tools.common import BIBLIOGRAPHY_REL
from agent_tools.envelope import fail, ok
from utils.api_citations.orchestrator import CitationResearcher, set_research_verbosity
from utils.citation_database import (
    CitationDatabase,
    add_citations_batch,
    create_empty_database,
    load_citation_database,
    save_citation_database,
)

CITATION_SUMMARY_REL = "drafts/citation_summary.md"


def _build_citation_summary(citation_database) -> str:
    """Build comprehensive citation database string for writing agent prompts."""
    citation_summary = f"\n\n{'=' * 80}\n## CITATION DATABASE - {len(citation_database.citations)} CITATIONS AVAILABLE\n{'=' * 80}\n\n"
    citation_summary += "\u26a0\ufe0f  **CRITICAL CITATION RESTRICTION** \u26a0\ufe0f\n\n"
    citation_summary += "You MUST ONLY cite papers from this database. DO NOT:\n"
    citation_summary += "- Cite papers from your training data\n"
    citation_summary += "- Invent or hallucinate citations\n"
    citation_summary += "- Reference papers not listed below\n"
    citation_summary += "- Use author names not in this database\n\n"
    citation_summary += (
        "Citation format: Use {{cite_XXX}} where XXX is the citation ID shown below.\n"
    )
    citation_summary += f"\n{'=' * 80}\n\n"

    for i, citation in enumerate(citation_database.citations, 1):
        authors_str = ", ".join(citation.authors[:3])
        if len(citation.authors) > 3:
            authors_str += " et al."

        citation_summary += f"{i}. **[{citation.id}]** {authors_str} ({citation.year})\n"
        citation_summary += f"   Title: {citation.title}\n"

        if citation.doi:
            citation_summary += f"   DOI: {citation.doi}\n"
        if citation.journal:
            citation_summary += f"   Journal: {citation.journal}\n"
        if citation.abstract:
            abstract_preview = citation.abstract[:300]
            if len(citation.abstract) > 300:
                abstract_preview += "..."
            citation_summary += f"   Abstract: {abstract_preview}\n"

        citation_summary += f"   Citation format: {{{{{citation.id}}}}}\n\n"

    citation_summary += f"\n{'=' * 80}\n"
    citation_summary += f"Total citations available: {len(citation_database.citations)}\n"
    citation_summary += "Remember: ONLY cite from this list. No external citations allowed.\n"
    citation_summary += f"{'=' * 80}\n"

    return citation_summary


@contextlib.contextmanager
def _quiet_stdout():
    """Keep stdout JSON-envelope-clean: redirect prints and detach stdout log handlers."""
    root_logger = logging.getLogger()
    stdout_handlers = [
        h
        for h in list(root_logger.handlers)
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
    ]
    for h in stdout_handlers:
        root_logger.removeHandler(h)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        for h in stdout_handlers:
            root_logger.addHandler(h)


# Quick reachability probe — fails fast (with a retryable verdict) instead of
# burning ~30s of per-API retries inside the researcher when the network is down.
PROBE_URL = "https://api.crossref.org/works"
PROBE_TIMEOUT = 5

DESCRIPTION = (
    "Search academic databases (Crossref, OpenAlex, Semantic Scholar, Google-grounded web "
    "search) for real papers matching a query, and ADD them to research/bibliography.json. "
    "Newly found citations get cite_XXX ids and become immediately citable by write_section — "
    "run this BEFORE citing a source you have not seen in the citation database, and never "
    "invent citations. Results are deduplicated against the existing database; "
    "drafts/citation_summary.md is rebuilt afterwards. Network/rate-limit failures are "
    "retryable. Requires no API key for academic sources; GEMINI_API_KEY only widens coverage."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 3,
            "description": "Search query — prefer specific author/year/keyword phrasing. "
            "If results are thin, rephrase rather than lowering min_results.",
        },
        "min_results": {
            "type": "integer",
            "default": 5,
            "description": "Desired minimum number of new citations (default 5). Advisory only — "
            "the response flags when fewer were found.",
        },
    },
    "required": ["query"],
}


def _probe_network() -> Optional[str]:
    """Return an error string if no network, else None."""
    try:
        requests.get(PROBE_URL, params={"rows": 0}, timeout=PROBE_TIMEOUT)
        return None
    except requests.exceptions.RequestException as e:
        return f"network unreachable ({type(e).__name__}: {e}) — check connectivity and retry"


def _load_db(root: Path) -> CitationDatabase:
    bib_path = Path(root) / BIBLIOGRAPHY_REL
    if bib_path.exists():
        return load_citation_database(bib_path)
    return create_empty_database()


def _max_cite_number(db: CitationDatabase) -> int:
    highest = 0
    for c in db.citations:
        cid = c.id or ""
        if cid.startswith("cite_") and cid[len("cite_") :].isdigit():
            highest = max(highest, int(cid[len("cite_") :]))
    return highest


def _rebuild_summary(root: Path, db: CitationDatabase) -> Optional[str]:
    """Best-effort rebuild of drafts/citation_summary.md. Returns a warning or None."""
    try:
        summary = _build_citation_summary(db)
        out = Path(root) / CITATION_SUMMARY_REL
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(summary, encoding="utf-8")
        return None
    except Exception as e:  # summary is auxiliary — never fail the whole search over it
        return f"could not rebuild {CITATION_SUMMARY_REL}: {type(e).__name__}: {e}"


def run(args: Dict, root: Path) -> Dict:
    query = args.get("query")
    if not isinstance(query, str) or len(query.strip()) < 3:
        return fail("query must be a string of at least 3 characters")

    raw_min = args.get("min_results", 5)
    try:
        min_results = int(raw_min)
    except (TypeError, ValueError):
        return fail("min_results must be an integer")
    if min_results < 1:
        return fail("min_results must be >= 1")
    query = query.strip()

    net_error = _probe_network()
    if net_error:
        return fail(f"search_literature failed: {net_error}", is_retryable=True)

    try:
        db = _load_db(root)
    except Exception as e:
        return fail(
            f"cannot load {BIBLIOGRAPHY_REL}: {type(e).__name__}: {e} — fix or remove the file",
            is_retryable=False,
        )

    try:
        # Mirror the pipeline config: LLM fallback stays disabled (it hallucinates
        # citations); academic APIs need no key, Gemini Grounded degrades gracefully.
        set_research_verbosity(False)
        with _quiet_stdout():
            researcher = CitationResearcher(
                gemini_model=None,
                enable_llm_fallback=False,
                verbose=False,
            )
            found = researcher.research_citation(query)
            researcher.close()
    except Exception as e:
        return fail(
            f"literature search failed: {type(e).__name__}: {e}",
            is_retryable=True,
        )

    base = _max_cite_number(db)  # next id follows the database's existing convention
    before = len(db.citations)
    added_count = add_citations_batch(db, found, deduplicate=True)
    added = db.citations[before : before + added_count]

    # Gap-free sequential cite_XXX ids in the order citations were added.
    for i, c in enumerate(added, start=1):
        c.id = f"cite_{base + i:03d}"

    if added_count > 0:
        try:
            save_citation_database(db, Path(root) / BIBLIOGRAPHY_REL)
        except Exception as e:
            return fail(
                f"citations found but could not save {BIBLIOGRAPHY_REL}: {type(e).__name__}: {e}",
                is_retryable=False,
            )

    warnings: List[str] = []
    if added_count < min_results:
        warnings.append(
            f"only {added_count} new citation(s) found (min_results={min_results}); "
            f"consider rephrasing the query"
        )
    summary_warning = _rebuild_summary(root, db)
    if summary_warning:
        warnings.append(summary_warning)

    new_citations = []
    for c in added:
        entry = {
            "id": c.id,
            "title": c.title,
            "authors": c.authors,
            "year": c.year,
        }
        if c.url:
            entry["url"] = c.url
        new_citations.append(entry)

    return ok(
        {
            "query": query,
            "new_citations": new_citations,
            "total_in_db": len(db.citations),
            "meets_min_results": added_count >= min_results,
            "warnings": warnings,
        }
    )


registry.register(
    registry.ToolSpec(
        name="search_literature",
        description=DESCRIPTION,
        input_schema=INPUT_SCHEMA,
        func=run,
    )
)
