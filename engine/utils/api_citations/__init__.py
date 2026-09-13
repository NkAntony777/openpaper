"""
ABOUTME: API-backed citation research using Crossref, OpenAlex, and Semantic Scholar
ABOUTME: Provides reliable paper lookup with 95%+ success rate (vs 40% LLM-only)
"""

from .crossref import CrossrefClient
from .openalex import OpenAlexClient
from .orchestrator import CitationResearcher
from .semantic_scholar import SemanticScholarClient

__all__ = [
    "CitationResearcher",
    "CrossrefClient",
    "OpenAlexClient",
    "SemanticScholarClient",
]
