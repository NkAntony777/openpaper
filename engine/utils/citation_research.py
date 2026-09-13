#!/usr/bin/env python3
"""
ABOUTME: Citation research orchestration — research_citations_via_api, the
ABOUTME: "Scout": deep-research planning (optional) followed by parallel or
ABOUTME: sequential query execution through the CitationResearcher API cascade,
ABOUTME: a relevance filter against the topic's anchor terms, a tiered quality
ABOUTME: gate, and Scout-compatible markdown output.

Split out of the former utils/agent_runner.py god object; the LLM-call half of
that module lives in utils/llm_runtime.py.
"""

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from concurrency.concurrency_config import get_concurrency_config
from utils.api_citations.orchestrator import CitationResearcher
from utils.citation_database import Citation
from utils.deep_research import DeepResearchPlanner
from utils.llm_runtime import safe_print

logger = logging.getLogger(__name__)

SOURCE_ORDER = ["Crossref", "Semantic Scholar", "Gemini Grounded", "Gemini LLM"]


# ------------------------------------------------------------------ relevance


def _extract_anchor_terms(topic: str) -> List[str]:
    if not topic:
        return []

    english_stop = {
        "the",
        "and",
        "with",
        "for",
        "from",
        "into",
        "using",
        "based",
        "approach",
        "system",
        "study",
        "research",
        "method",
        "model",
        "framework",
        "analysis",
        "design",
        "application",
        "applications",
        "survey",
        "review",
    }
    generic_cn = {
        "研究",
        "系统",
        "方法",
        "模型",
        "架构",
        "设计",
        "分析",
        "应用",
        "探索",
        "综述",
    }
    cn_particles = [
        "的",
        "和",
        "与",
        "及",
        "以及",
        "基于",
        "在",
        "于",
        "对",
        "通过",
        "结合",
        "融合",
    ]

    anchors: List[str] = []
    seen = set()

    topic_lower = topic.lower()
    for token in re.findall(r"[a-z0-9][a-z0-9\-]{1,}", topic_lower):
        token = token.strip("-")
        if len(token) < 3 or token in english_stop:
            continue
        if token not in seen:
            anchors.append(token)
            seen.add(token)

    cn_segments = re.findall(r"[\u4e00-\u9fff]+", topic)
    if cn_segments:
        split_re = "|".join(re.escape(p) for p in cn_particles)
        for segment in cn_segments:
            parts = re.split(split_re, segment)
            for part in parts:
                part = part.strip()
                if len(part) < 2 or part in generic_cn:
                    continue
                if part not in seen:
                    anchors.append(part)
                    seen.add(part)

    return anchors


def _contains_term(text: str, term: str) -> bool:
    if not text or not term:
        return False
    if any(ord(c) > 127 for c in term) or " " in term or "-" in term:
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def _is_citation_relevant(citation: Citation, anchor_terms: List[str]) -> bool:
    if not anchor_terms:
        return True
    parts = [
        citation.title or "",
        getattr(citation, "abstract", "") or "",
        getattr(citation, "journal", "") or "",
        getattr(citation, "publisher", "") or "",
    ]
    text = " ".join(parts).lower()
    matches = sum(1 for term in anchor_terms if _contains_term(text, term))
    return matches >= 1


def _fallback_queries_from_topic(topic: str) -> List[str]:
    """Basic queries generated from the topic when deep-research planning fails.

    Shared by the timeout and generic-exception fallback paths: 5 aspect queries
    plus key-phrase queries for long topics, padded to at least 10 entries.
    """
    topic_words = topic.split()
    queries = [
        topic,
        f"{topic} research",
        f"{topic} analysis",
        f"{topic} review",
        f"{topic} study",
    ]

    if len(topic_words) > 5:
        key_phrases = []
        if len(topic_words) >= 3:
            key_phrases.append(" ".join(topic_words[:3]))
        if len(topic_words) >= 6:
            key_phrases.append(" ".join(topic_words[2:5]))
        if len(topic_words) >= 4:
            key_phrases.append(" ".join(topic_words[-3:]))
        queries.extend(f"{phrase} research" for phrase in key_phrases)

    while len(queries) < 10:
        queries.append(f"{topic} {len(queries)}")

    return queries


# ------------------------------------------------------------------- planning


def _plan_deep_research_queries(
    model: Any,
    topic: str,
    scope: Optional[str],
    seed_references: Optional[List[str]],
    min_sources_deep: int,
    verbose: bool,
) -> Tuple[List[str], Optional[Dict[str, Any]]]:
    """Run DeepResearchPlanner with validate→refine→validate; fall back to basic
    topic queries on timeout or planning failure. Returns (queries, plan)."""
    if verbose:
        safe_print("\n🧠 Deep Research Planning Phase")
        safe_print(f"{'=' * 80}")
        safe_print("\n📋 Input:")
        safe_print(f"   Topic: {topic}")
        if scope:
            safe_print(f"   Scope: {scope}")
        if seed_references:
            safe_print(f"   Seed References: {len(seed_references)}")
        safe_print(f"   Target: {min_sources_deep}+ sources")
        safe_print()

    planner = DeepResearchPlanner(gemini_model=model, min_sources=min_sources_deep, verbose=verbose)

    try:
        plan = planner.create_research_plan(
            topic=topic, scope=scope, seed_references=seed_references
        )

        if not planner.validate_plan(plan):
            if verbose:
                safe_print("\n⚠️  Initial plan validation failed - attempting refinement...")
            plan = planner.refine_plan(
                plan=plan,
                feedback=f"Insufficient queries or coverage. Need minimum {min_sources_deep} sources. "
                f"Generate more diverse queries covering: author searches, title searches, "
                f"topic queries, regulatory/standards, and interdisciplinary connections.",
            )
            if not planner.validate_plan(plan):
                raise ValueError(
                    f"Deep research plan validation failed after refinement. "
                    f"Generated {len(plan.get('queries', []))} queries, "
                    f"estimated {planner.estimate_coverage(plan.get('queries', []))} sources, "
                    f"but need minimum {min_sources_deep}."
                )

        queries = plan.get("queries", [])

        if verbose:
            safe_print("\n✅ Research Plan Created:")
            safe_print(f"   Queries Generated: {len(queries)}")
            safe_print(f"   Estimated Coverage: {planner.estimate_coverage(queries)} sources")
            safe_print("\n📝 Research Strategy:")
            strategy_lines = plan.get("strategy", "").split("\n")
            for line in strategy_lines[:5]:  # First 5 lines
                safe_print(f"   {line}")
            if len(strategy_lines) > 5:
                safe_print("   ... (see output file for full strategy)")
            safe_print()

        return queries, plan

    except (TimeoutError, FuturesTimeoutError) as e:
        logger.warning(f"Deep research planning timed out, falling back to standard mode: {e}")
        fallback_reason = "Deep Research Planning Timeout"
    except Exception as e:
        logger.warning(f"Deep research planning failed, falling back to standard mode: {e}")
        fallback_reason = f"Deep Research Planning Failed — Error: {str(e)[:200]}"

    # Fallback: basic queries derived from the topic itself
    if verbose:
        safe_print(f"\n⚠️  {fallback_reason}")
        safe_print("   Falling back to standard mode with basic queries...")
        safe_print()
    queries = _fallback_queries_from_topic(topic)
    if verbose:
        safe_print(f"   Generated {len(queries)} fallback queries")
        safe_print()
    return queries, None


# --------------------------------------------------------------- result merge


def _merge_topic_results(
    citations_list: List[Citation],
    anchor_terms: List[str],
    sources_breakdown: Dict[str, int],
    citations: List[Citation],
    indent: str,
    verbose: bool,
) -> bool:
    """Apply the relevance filter, extend the citation pool and source breakdown,
    and print the per-query result line. Returns True when at least one relevant
    citation was merged; the caller records the topic as failed otherwise."""
    if anchor_terms:
        before_filter = len(citations_list)
        citations_list = [c for c in citations_list if _is_citation_relevant(c, anchor_terms)]
        filtered_out = before_filter - len(citations_list)
        if filtered_out and verbose:
            safe_print(f"{indent}⚠️  Filtered {filtered_out} off-topic citations")

    if not citations_list:
        return False

    # Add ALL citations from this query (multiple sources)
    citations.extend(citations_list)
    for citation in citations_list:
        source = citation.api_source or "Unknown"
        if source in sources_breakdown:
            sources_breakdown[source] += 1

    if verbose:
        sources_str = ", ".join(c.api_source or "Unknown" for c in citations_list)
        first_citation = citations_list[0]
        authors_str = first_citation.authors[0] if first_citation.authors else "Unknown"
        count_str = f" (+{len(citations_list) - 1} more)" if len(citations_list) > 1 else ""
        safe_print(
            f"{indent}✅ {authors_str} et al. ({first_citation.year}) [{sources_str}]{count_str}"
        )
    return True


# ----------------------------------------------------------------- quality gate


def _evaluate_quality_gate(
    citation_count: int,
    target_minimum: int,
    minimal_threshold: int,
    acceptable_threshold: int,
    failed_topics: List[str],
    enforce_quality_gate: bool,
    verbose: bool,
) -> None:
    """Tiered citation-count gate: excellent / acceptable / minimal / fail.
    Raises ValueError on failure when enforce_quality_gate is set."""
    if citation_count >= target_minimum:
        if verbose:
            safe_print(
                f"✅ QUALITY GATE PASSED (EXCELLENT): {citation_count} ≥ {target_minimum} required\n"
            )
        logger.info(f"Quality gate: EXCELLENT - {citation_count}/{target_minimum} citations")
        return

    percentage = (citation_count / target_minimum) * 100 if target_minimum else 0.0

    if citation_count >= acceptable_threshold:
        if verbose:
            safe_print(
                f"⚠️  QUALITY GATE PASSED (ACCEPTABLE): {citation_count}/{target_minimum} ({percentage:.1f}%)"
            )
            safe_print(
                f"    Academic quality is good, but {target_minimum - citation_count} more citations recommended.\n"
            )
        logger.warning(
            f"Quality gate: ACCEPTABLE - {citation_count}/{target_minimum} ({percentage:.1f}%)"
        )
        return

    if citation_count >= minimal_threshold:
        if verbose:
            safe_print(
                f"⚠️  QUALITY GATE PASSED (MINIMAL): {citation_count}/{target_minimum} ({percentage:.1f}%)"
            )
            safe_print("    ⚠️  WARNING: Citation count is below recommended standards.")
            safe_print(
                f"    Consider adding {target_minimum - citation_count} more citations for better academic rigor.\n"
            )
        logger.warning(
            f"Quality gate: MINIMAL - {citation_count}/{target_minimum} ({percentage:.1f}%) - below standards"
        )
        return

    error_msg = (
        f"\n❌ QUALITY GATE FAILED (INSUFFICIENT CITATIONS)\n\n"
        f"Only {citation_count} citations found ({percentage:.1f}%), "
        f"but minimum {minimal_threshold} required ({minimal_threshold / target_minimum * 100:.0f}% of target).\n"
        f"Target: {target_minimum} citations (100%)\n"
        f"Acceptable: {acceptable_threshold}+ citations (86%)\n"
        f"Minimal: {minimal_threshold}+ citations (70%)\n"
        f"Current: {citation_count} citations ({percentage:.1f}%) ❌\n\n"
        f"Academic draft standards require at least {minimal_threshold} citations.\n\n"
        f"Failed Topics ({len(failed_topics)}):\n"
    )
    for failed_topic in failed_topics[:10]:
        error_msg += f"  - {failed_topic}\n"
    if len(failed_topics) > 10:
        error_msg += f"  ... and {len(failed_topics) - 10} more\n"

    if enforce_quality_gate:
        logger.error(
            f"Quality gate FAILED: {citation_count} < {minimal_threshold} (minimal threshold)"
        )
        raise ValueError(error_msg)
    logger.warning(
        f"Quality gate bypassed: {citation_count} < {minimal_threshold} (minimal threshold)"
    )
    if verbose:
        safe_print(
            "⚠️  QUALITY GATE BYPASSED: Strict validation disabled; continuing with fewer citations.\n"
        )


# --------------------------------------------------------------------- output


def _format_scout_markdown(
    citation_count: int,
    success_rate: float,
    sources_breakdown: Dict[str, int],
    citations: List[Citation],
    failed_topics: List[str],
) -> str:
    lines = [
        "# Scout Output - Academic Citation Discovery",
        "",
        "## Summary",
        "",
        f"**Total Valid Citations**: {citation_count}",
        f"**Success Rate**: {success_rate:.1f}%",
        f"**Failed Topics**: {len(failed_topics)}",
        "",
        "### Sources Breakdown",
        "",
    ]

    for source, count in sources_breakdown.items():
        percentage = (count / citation_count * 100) if citation_count > 0 else 0
        lines.append(f"- **{source}**: {count} ({percentage:.1f}%)")

    lines.extend(["", "---", "", "## Citations Found", ""])

    # Add citations grouped by source
    for source in SOURCE_ORDER:
        source_citations = [c for c in citations if c.api_source == source]
        if not source_citations:
            continue

        lines.append(f"### From {source} ({len(source_citations)} citations)")
        lines.append("")

        for idx, citation in enumerate(source_citations, 1):
            lines.append(f"#### {idx}. {citation.title}")
            lines.append(f"**Authors**: {', '.join(citation.authors)}")
            lines.append(f"**Year**: {citation.year}")
            lines.append(f"**DOI**: {citation.doi}")
            if citation.url:
                lines.append(f"**URL**: {citation.url}")
            if hasattr(citation, "abstract") and citation.abstract:
                # Include abstract for Scribe to summarize (prevent hallucination)
                lines.append("")
                lines.append(f"**Abstract**: {citation.abstract}")
            lines.append("")

    if failed_topics:
        lines.extend(
            [
                "---",
                "",
                "## Failed Topics",
                "",
                "The following topics did not return valid citations:",
                "",
            ]
        )
        lines.extend(f"- {t}" for t in failed_topics)

    return "\n".join(lines)


# ------------------------------------------------------------------ main entry


def research_citations_via_api(
    model: Any,
    research_topics: Optional[List[str]] = None,
    output_path: Optional[Path] = None,
    target_minimum: int = 50,
    verbose: bool = True,
    enforce_quality_gate: bool = True,
    # Deep Research Mode parameters
    use_deep_research: bool = False,
    topic: Optional[str] = None,
    scope: Optional[str] = None,
    seed_references: Optional[List[str]] = None,
    min_sources_deep: int = 100,
    # Timeout control
    per_topic_timeout_seconds: int = 90,  # Increased from 30s - citations need time to search multiple APIs
    # Progress reporting
    progress_callback: Optional[Callable[[str, str], None]] = None,
) -> Dict[str, Any]:
    """
    Research citations using API-backed fallback chain with optional deep research mode.

    Two modes of operation:
    1. **Standard Mode** (use_deep_research=False):
       - Uses manually provided research_topics list
       - Executes each topic through API fallback chain
       - Best for targeted, curated research queries

    2. **Deep Research Mode** (use_deep_research=True):
       - Uses DeepResearchPlanner for autonomous research strategy
       - Creates 50+ systematic queries from topic + scope + seed references
       - Best for comprehensive literature reviews (dissertations, draft)

    API Fallback Chain:
    Crossref → Semantic Scholar → Gemini Grounded → Gemini LLM (95%+ success rate)

    Args:
        model: Configured Gemini model instance (used for planning and LLM fallback)
        research_topics: List of research topics (required if use_deep_research=False)
        output_path: Optional path to save Scout-compatible markdown output
        target_minimum: Minimum citations required to pass quality gate (default: 50)
        verbose: Whether to print progress messages (default: True)
        enforce_quality_gate: Raise on quality-gate failure instead of warning (default: True)

        use_deep_research: Enable deep research mode (default: False)
        topic: Main research topic (required if use_deep_research=True)
        scope: Optional research scope constraints (e.g., "EU focus; B2C and B2B")
        seed_references: Optional seed papers to expand from
        min_sources_deep: Minimum sources for deep research (default: 100)
        per_topic_timeout_seconds: Maximum time to spend on each research topic (default: 90)
        progress_callback: Optional callback(message, event_type) for progress reporting

    Returns:
        Dict with keys:
            - citations: List[Citation] - Valid citations found
            - count: int - Number of valid citations
            - sources: Dict[str, int] - Breakdown by source (Crossref, Semantic Scholar, etc.)
            - failed_topics: List[str] - Topics that failed to find citations
            - research_plan: Optional[Dict] - Deep research plan (if deep mode enabled)

    Raises:
        ValueError: If citation count < target_minimum (quality gate failure)
        ValueError: If invalid mode parameters (missing required args)
    """
    # Validate mode parameters
    if use_deep_research:
        if not topic:
            raise ValueError("Deep research mode requires 'topic' parameter")
        mode_name = "DEEP RESEARCH MODE"
    else:
        if not research_topics:
            raise ValueError("Standard mode requires 'research_topics' parameter")
        mode_name = "STANDARD MODE"

    if verbose:
        safe_print("\n" + "=" * 80)
        safe_print(f"🔬 API-BACKED SCOUT - {mode_name}")
        safe_print("=" * 80)

    # Deep Research Mode: autonomous research planning (falls back to basic
    # topic-derived queries on planner failure/timeout)
    research_plan: Optional[Dict[str, Any]] = None
    if use_deep_research:
        research_topics, research_plan = _plan_deep_research_queries(
            model,
            topic,
            scope,
            seed_references,
            min_sources_deep,
            verbose,
        )

    # Execution Phase: run queries through the API fallback chain
    if verbose:
        safe_print("\n📊 Execution Configuration:")
        safe_print(f"   Target Minimum: {target_minimum} citations")
        safe_print(f"   Research Topics/Queries: {len(research_topics)}")
        if output_path:
            safe_print(f"   Output: {output_path}")
        safe_print()

    # Initialize CitationResearcher with API fallback chain.
    # Semantic Scholar can be disabled via env var if rate limited (403 errors)
    enable_semantic_scholar = os.environ.get("ENABLE_SEMANTIC_SCHOLAR", "true").lower() != "false"

    researcher = CitationResearcher(
        gemini_model=model,
        enable_crossref=True,
        enable_semantic_scholar=enable_semantic_scholar,
        enable_gemini_grounded=True,  # Enable for industry reports (McKinsey, Gartner, etc.)
        enable_smart_routing=True,  # Enable query classification for source diversity
        enable_llm_fallback=False,  # DISABLED: LLM hallucinates citations
        use_serper=True,  # Enable Serper API for web search fallback
        verbose=verbose,
        progress_callback=progress_callback,  # Pass through for progress reporting
    )

    if not enable_semantic_scholar and verbose:
        safe_print("   ⚠️  Semantic Scholar disabled (ENABLE_SEMANTIC_SCHOLAR=false)")

    citations: List[Citation] = []
    sources_breakdown: Dict[str, int] = {source: 0 for source in SOURCE_ORDER}
    failed_topics: List[str] = []
    base_topic = scope or topic or (research_topics[0] if research_topics else "")
    anchor_terms = _extract_anchor_terms(base_topic)
    if verbose and anchor_terms:
        safe_print(f"   Relevance filter enabled (anchors: {', '.join(anchor_terms)})")

    # Parallel citation research configuration (tier-adaptive)
    config = get_concurrency_config(verbose=False)
    BATCH_SIZE = config.scout_batch_size
    BATCH_DELAY = config.scout_batch_delay
    PARALLEL_WORKERS = config.scout_parallel_workers

    # Detect if proxies are configured for rate limit bypass.
    # With proxies: skip delays for maximum throughput; without: respect API limits
    from utils.api_citations.base import PROXY_LIST

    use_proxies = len(PROXY_LIST) > 0
    effective_batch_delay = 0 if use_proxies else BATCH_DELAY

    if verbose and use_proxies:
        safe_print(f"\n🔀 Proxy rotation enabled: {len(PROXY_LIST)} proxies")
        safe_print("   Batch delays disabled for maximum throughput")

    def _research_single_topic(
        topic_with_idx: Tuple[int, str],
    ) -> Tuple[int, str, List[Citation], Optional[str]]:
        """Research a single topic with timeout. Returns (idx, topic, list_of_citations, error_or_None)."""
        idx, research_topic = topic_with_idx
        try:
            # Wrap in executor for timeout control
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(researcher.research_citation, research_topic)
                try:
                    citations_list = future.result(timeout=per_topic_timeout_seconds)
                    return (idx, research_topic, citations_list, None)
                except FuturesTimeoutError:
                    return (idx, research_topic, [], f"Timeout after {per_topic_timeout_seconds}s")
        except Exception as e:
            return (idx, research_topic, [], str(e))

    # Early stopping at 50 citations
    early_stop_threshold = 50

    # Parallel or sequential based on config
    if PARALLEL_WORKERS > 1:
        if verbose:
            safe_print(f"\n🚀 Parallel citation research enabled ({PARALLEL_WORKERS} workers)")

        total_topics = len(research_topics)

        for batch_start in range(0, total_topics, BATCH_SIZE):
            # Early stopping: check if we've reached the target
            if len(citations) >= early_stop_threshold:
                if verbose:
                    safe_print(
                        f"\n⏩ Early stopping: {len(citations)} citations collected "
                        f"(target: {target_minimum}, threshold: {early_stop_threshold})"
                    )
                break

            batch_end = min(batch_start + BATCH_SIZE, total_topics)
            batch = list(enumerate(research_topics[batch_start:batch_end], batch_start + 1))

            if verbose and batch_start > 0 and effective_batch_delay > 0:
                safe_print(
                    f"\n⏸️  Batch complete ({batch_start} topics processed). "
                    f"Waiting {effective_batch_delay}s to respect API limits..."
                )
                time.sleep(effective_batch_delay)

            if verbose:
                safe_print(
                    f"\n📦 Processing batch {batch_start // BATCH_SIZE + 1} ({len(batch)} topics)..."
                )

            # Execute batch in parallel
            with ThreadPoolExecutor(max_workers=PARALLEL_WORKERS) as executor:
                futures = {executor.submit(_research_single_topic, item): item for item in batch}

                for future in as_completed(futures):
                    idx, research_topic, citations_list, error = future.result()

                    if verbose:
                        safe_print(
                            f"[{idx}/{total_topics}] 🔎 {research_topic[:55]}"
                            f"{'...' if len(research_topic) > 55 else ''}",
                            end=" ",
                        )

                    if error:
                        failed_topics.append(research_topic)
                        if verbose:
                            safe_print(f"❌ Error: {error[:30]}...")
                        logger.error(f"Citation research failed for '{research_topic}': {error}")
                        continue

                    if citations_list and _merge_topic_results(
                        citations_list,
                        anchor_terms,
                        sources_breakdown,
                        citations,
                        indent="",
                        verbose=verbose,
                    ):
                        # Check for early stopping within batch
                        if len(citations) >= early_stop_threshold:
                            if verbose:
                                safe_print(
                                    f"\n⏩ Early stopping: {len(citations)} citations collected"
                                )
                            break
                    else:
                        failed_topics.append(research_topic)
                        if verbose:
                            safe_print(
                                "❌ No relevant citation found"
                                if citations_list
                                else "❌ No citation found"
                            )
    else:
        # Sequential execution (free tier or 1 worker)
        if verbose:
            safe_print("\n🔄 Sequential citation research (1 worker)")

        for idx, research_topic in enumerate(research_topics, 1):
            # Early stopping: check if we've reached the target
            if len(citations) >= early_stop_threshold:
                if verbose:
                    safe_print(
                        f"\n⏩ Early stopping: {len(citations)} citations collected "
                        f"(target: {target_minimum}, threshold: {early_stop_threshold})"
                    )
                break

            # Add delay every BATCH_SIZE topics to prevent burst rate limits
            if idx > 1 and (idx - 1) % BATCH_SIZE == 0 and effective_batch_delay > 0:
                if verbose:
                    safe_print(
                        f"\n⏸️  Batch complete ({idx - 1} topics processed). "
                        f"Waiting {effective_batch_delay}s to respect API limits..."
                    )
                time.sleep(effective_batch_delay)

            if verbose:
                safe_print(
                    f"[{idx}/{len(research_topics)}] 🔎 {research_topic[:65]}"
                    f"{'...' if len(research_topic) > 65 else ''}"
                )

            try:
                _, _, citations_list, error = _research_single_topic((idx, research_topic))
                if error and not citations_list:
                    failed_topics.append(research_topic)
                    if error.startswith("Timeout after"):
                        if verbose:
                            safe_print(f"    ⏱️  Timeout after {per_topic_timeout_seconds}s")
                        logger.warning(
                            f"Citation research timed out for '{research_topic}' "
                            f"after {per_topic_timeout_seconds}s"
                        )
                    else:
                        if verbose:
                            safe_print(f"    ❌ Error: {error}")
                        logger.error(f"Citation research failed for '{research_topic}': {error}")
                    continue

                if citations_list and _merge_topic_results(
                    citations_list,
                    anchor_terms,
                    sources_breakdown,
                    citations,
                    indent="    ",
                    verbose=verbose,
                ):
                    continue

                failed_topics.append(research_topic)
                if verbose:
                    safe_print(
                        "    ❌ No citation found"
                        if not citations_list
                        else "    ❌ No relevant citation found"
                    )

            except Exception as e:
                failed_topics.append(research_topic)
                if verbose:
                    safe_print(f"    ❌ Error: {str(e)}")
                logger.error(f"Citation research failed for '{research_topic}': {str(e)}")

    # Calculate success metrics
    citation_count = len(citations)
    success_rate = (citation_count / len(research_topics) * 100) if research_topics else 0

    if verbose:
        safe_print("\n" + "=" * 80)
        safe_print("📊 SCOUT RESULTS")
        safe_print("=" * 80)
        safe_print(f"\n✅ Valid Citations: {citation_count}")
        safe_print(f"❌ Failed Topics: {len(failed_topics)}")
        safe_print(f"📈 Success Rate: {success_rate:.1f}%")
        safe_print("\n📚 Sources Breakdown:")
        for source, count in sources_breakdown.items():
            percentage = (count / citation_count * 100) if citation_count > 0 else 0
            safe_print(f"   {source}: {count} ({percentage:.1f}%)")
        safe_print()

    # Tiered Quality Gate
    _evaluate_quality_gate(
        citation_count,
        target_minimum,
        minimal_threshold=int(target_minimum * 0.70),
        acceptable_threshold=int(target_minimum * 0.86),
        failed_topics=failed_topics,
        enforce_quality_gate=enforce_quality_gate,
        verbose=verbose,
    )

    # Format and write Scout-compatible markdown (optional)
    if output_path is not None:
        markdown_content = _format_scout_markdown(
            citation_count,
            success_rate,
            sources_breakdown,
            citations,
            failed_topics,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown_content, encoding="utf-8")

        if verbose:
            safe_print(f"💾 Saved Scout output to: {output_path}")
            safe_print(f"   File size: {output_path.stat().st_size:,} bytes\n")

    logger.info(f"Scout completed: {citation_count} citations, {success_rate:.1f}% success rate")

    return {
        "citations": citations,
        "count": citation_count,
        "sources": sources_breakdown,
        "failed_topics": failed_topics,
        "research_plan": research_plan,
    }
