#!/usr/bin/env python3
"""
ABOUTME: ResearchBrief — structured research intent for expert users
ABOUTME: Carries RQs, hypotheses, tasks, innovations, baselines, ablations, metrics,
ABOUTME: split strategy, GT protocol, forbidden claims and venue target through the pipeline.

Priority contract (see docs/OPTIMIZATION_DIRECTIONS.md):
    research_brief > blurb > topic alone

If a brief is provided, it takes precedence over `topic` + `blurb` everywhere:
research queries, outline generation, compose prompts and validation all
consume the structured fields instead of guessing.
"""

import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sub-structures
# ---------------------------------------------------------------------------

@dataclass
class Hypothesis:
    """A single research hypothesis (H1, H2, ...)."""
    statement: str
    id: Optional[str] = None          # "H1", "H2", ...; auto-assigned if None
    rationale: Optional[str] = None   # why this hypothesis is expected


@dataclass
class ResearchTask:
    """A concrete task the research addresses (Task 1, 2, ...)."""
    name: str
    description: Optional[str] = None
    id: Optional[str] = None          # "T1", "T2", ...; auto-assigned if None


@dataclass
class MetricSpec:
    """An evaluation metric with optional parameters and priority."""
    name: str                          # "Hits@K", "MRR", "F1", ...
    description: Optional[str] = None
    k_value: Optional[int] = None      # K for Hits@K / Recall@K
    priority: Optional[str] = None     # "primary" / "secondary"


@dataclass
class BaselineSpec:
    """A baseline method the paper must compare against."""
    name: str                          # "TGN (Rossi et al., 2020)"
    category: Optional[str] = None     # "rule" / "ml" / "sequence" / "temporal-gnn" / ...
    description: Optional[str] = None


@dataclass
class AblationSpec:
    """One ablation dimension (e.g. "w/o Global Context")."""
    name: str                          # "w/o Duration"
    dimension: Optional[str] = None    # which component is removed
    purpose: Optional[str] = None      # what removing it is meant to show


@dataclass
class SectionSpec:
    """A user-specified outline section (custom_outline support)."""
    title: str
    role: Optional[str] = None         # maps to a standard slot, see STANDARD_ROLES
    target_words: Optional[int] = None
    required_subsections: List[str] = field(default_factory=list)
    specific_citations: List[str] = field(default_factory=list)
    writing_style_hint: Optional[str] = None
    content_notes: Optional[str] = None


# Standard compose slots a custom section can map onto. Order matters:
# it is the order sections appear in the generated outline.
STANDARD_ROLES = (
    "introduction",
    "literature_review",
    "methodology",
    "results",
    "discussion",
    "conclusion",
    "appendix",
)

# Fuzzy title keywords -> standard role, used when `role` is not given.
_ROLE_KEYWORDS = {
    "introduction": ("intro",),
    "literature_review": ("literature", "related work", "background"),
    "methodology": ("method", "approach", "design", "framework"),
    "results": ("result", "evaluation", "experiment", "analysis"),
    "discussion": ("discussion", "limitation", "implication"),
    "conclusion": ("conclusion", "summary", "future work"),
    "appendix": ("appendix", "supplementary"),
}


def infer_role(title: str) -> Optional[str]:
    """Best-effort mapping of a free-form section title to a standard role."""
    lowered = title.lower()
    for role, keywords in _ROLE_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return role
    return None


def _as_spec_list(raw: Optional[List[Any]], spec_cls, name_field: str) -> List[Any]:
    """Normalize a mixed list of plain strings / dicts / spec instances into spec instances."""
    if not raw:
        return []
    result = []
    for i, item in enumerate(raw):
        if isinstance(item, spec_cls):
            result.append(item)
        elif isinstance(item, str):
            kwargs = {name_field: item}
            result.append(spec_cls(**kwargs))
        elif isinstance(item, dict):
            known = {f for f in item if f in spec_cls.__dataclass_fields__}
            result.append(spec_cls(**{k: item[k] for k in known}))
        else:
            raise ValueError(
                f"Item {i} must be a str, dict or {spec_cls.__name__}, got {type(item).__name__}"
            )
    return result


# ---------------------------------------------------------------------------
# ResearchBrief
# ---------------------------------------------------------------------------

@dataclass
class ResearchBrief:
    """
    Structured research brief covering the full research intent of a paper.

    All fields are optional; only the ones the user cares about need to be set.
    Consumed by: research phase (queries), structure phase (outline),
    compose phase (prompts), validate phase (GT protocol + forbidden claims).
    """

    title: Optional[str] = None                        # paper title; overrides topic
    core_question: Optional[str] = None                # one-sentence research question
    research_questions: List[str] = field(default_factory=list)     # RQ1, RQ2, ...
    hypotheses: List[Hypothesis] = field(default_factory=list)      # H1, H2, ...
    tasks: List[ResearchTask] = field(default_factory=list)         # Task 1, 2, ...
    innovations: List[str] = field(default_factory=list)            # contribution 1, 2, 3
    baselines: List[BaselineSpec] = field(default_factory=list)
    ablation_dims: List[AblationSpec] = field(default_factory=list)
    metrics: List[MetricSpec] = field(default_factory=list)
    split_strategy: Optional[str] = None               # "temporal" / "user" / "scene" / free text
    ground_truth_protocol: Optional[str] = None        # where ground truth comes from
    forbidden_claims: List[str] = field(default_factory=list)       # must NOT appear in the paper
    venue_target: Optional[str] = None                 # "ICWSM" / "WWW" / "KDD" / ...
    literature_search_questions: List[str] = field(default_factory=list)
    output_sections: List[SectionSpec] = field(default_factory=list)  # custom outline
    additional_context: Optional[str] = None           # free-form extra context

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def __post_init__(self):
        self.hypotheses = _as_spec_list(self.hypotheses, Hypothesis, "statement")
        self.tasks = _as_spec_list(self.tasks, ResearchTask, "name")
        self.baselines = _as_spec_list(self.baselines, BaselineSpec, "name")
        self.ablation_dims = _as_spec_list(self.ablation_dims, AblationSpec, "name")
        self.metrics = _as_spec_list(self.metrics, MetricSpec, "name")
        self.output_sections = _as_spec_list(self.output_sections, SectionSpec, "title")

        for i, h in enumerate(self.hypotheses, start=1):
            if not h.id:
                h.id = f"H{i}"
        for i, t in enumerate(self.tasks, start=1):
            if not t.id:
                t.id = f"T{i}"
        for s in self.output_sections:
            if not s.role:
                s.role = infer_role(s.title)

    @property
    def effective_topic(self) -> Optional[str]:
        """The topic string to use when a brief is provided."""
        return self.title or self.core_question

    def is_empty(self) -> bool:
        """True if the brief carries no meaningful research intent."""
        meaningful = (
            self.title, self.core_question, self.research_questions, self.hypotheses,
            self.tasks, self.innovations, self.baselines, self.ablation_dims,
            self.metrics, self.split_strategy, self.ground_truth_protocol,
            self.forbidden_claims, self.venue_target,
            self.literature_search_questions, self.output_sections,
            self.additional_context,
        )
        return not any(meaningful)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResearchBrief":
        """Build a brief from a plain dict, tolerating str/dict/list values."""
        if not isinstance(data, dict):
            raise ValueError(f"ResearchBrief.from_dict expects a dict, got {type(data).__name__}")

        # Alias: negative_claims (doc P1 naming) -> forbidden_claims (doc P4 naming)
        if "negative_claims" in data and "forbidden_claims" not in data:
            data = {**data, "forbidden_claims": data["negative_claims"]}
        # Alias: custom_outline / sections -> output_sections
        for alias in ("custom_outline", "sections"):
            if alias in data and "output_sections" not in data:
                data = {**data, "output_sections": data[alias]}
        # Alias: search_questions -> literature_search_questions
        if "search_questions" in data and "literature_search_questions" not in data:
            data = {**data, "literature_search_questions": data["search_questions"]}

        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        unknown = set(data) - set(known)
        if unknown:
            logger.warning(f"ResearchBrief.from_dict ignoring unknown keys: {sorted(unknown)}")
        return cls(**known)

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> "ResearchBrief":
        import yaml  # optional dependency; project requirements include PyYAML
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_json(cls, path: Union[str, Path]) -> "ResearchBrief":
        import json
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "ResearchBrief":
        """Load from .yaml / .yml / .json based on extension."""
        p = Path(path)
        if p.suffix.lower() in (".yaml", ".yml"):
            return cls.from_yaml(p)
        if p.suffix.lower() == ".json":
            return cls.from_json(p)
        raise ValueError(f"Unsupported brief file extension: {p.suffix} (use .yaml/.yml/.json)")

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def to_prompt_context(self) -> str:
        """
        Render the brief as a structured markdown block for LLM prompts.

        Only non-empty fields are included, so a sparse brief yields a short block.
        """
        blocks: List[str] = []

        if self.core_question:
            blocks.append(f"**Core research question:** {self.core_question}")

        if self.research_questions:
            lines = "\n".join(
                f"- {rq if str(rq).upper().startswith('RQ') else f'RQ{i}: {rq}'}"
                for i, rq in enumerate(self.research_questions, start=1)
            )
            blocks.append(f"**Research questions:**\n{lines}")

        if self.hypotheses:
            lines = []
            for h in self.hypotheses:
                line = f"- {h.id}: {h.statement}" if h.id else f"- {h.statement}"
                if h.rationale:
                    line += f" (rationale: {h.rationale})"
                lines.append(line)
            blocks.append(f"**Hypotheses (must be explicitly addressed):**\n" + "\n".join(lines))

        if self.tasks:
            lines = []
            for t in self.tasks:
                line = f"- {t.id}: {t.name}" if t.id else f"- {t.name}"
                if t.description:
                    line += f" — {t.description}"
                lines.append(line)
            blocks.append(f"**Research tasks (structure the work around these):**\n" + "\n".join(lines))

        if self.innovations:
            lines = "\n".join(f"- {inn}" for inn in self.innovations)
            blocks.append(
                f"**Stated contributions/innovations (the paper's novelty claims — "
                f"use these exact framings, do not invent others):**\n{lines}"
            )

        if self.baselines:
            lines = []
            for b in self.baselines:
                line = f"- {b.name}"
                if b.category:
                    line += f" [{b.category}]"
                if b.description:
                    line += f": {b.description}"
                lines.append(line)
            blocks.append(
                f"**Required baselines (Methods/Results must cover exactly these):**\n" + "\n".join(lines)
            )

        if self.ablation_dims:
            lines = []
            for a in self.ablation_dims:
                line = f"- {a.name}"
                if a.dimension:
                    line += f" (removes: {a.dimension})"
                if a.purpose:
                    line += f" — {a.purpose}"
                lines.append(line)
            blocks.append(
                f"**Required ablation studies (cover exactly these dimensions):**\n" + "\n".join(lines)
            )

        if self.metrics:
            lines = []
            for m in self.metrics:
                line = f"- {m.name}"
                if m.k_value is not None:
                    line += f" (K={m.k_value})"
                if m.priority:
                    line += f" [{m.priority}]"
                if m.description:
                    line += f": {m.description}"
                lines.append(line)
            blocks.append(
                f"**Evaluation metrics (use these, in this priority order; "
                f"do not silently substitute others):**\n" + "\n".join(lines)
            )

        if self.split_strategy:
            blocks.append(
                f"**Data split strategy:** {self.split_strategy} "
                f"(respect this split when discussing evaluation; do not describe random splits)"
            )

        if self.ground_truth_protocol:
            blocks.append(
                f"**Ground truth protocol:** {self.ground_truth_protocol} "
                f"(all empirical claims must trace back to this GT source)"
            )

        if self.forbidden_claims:
            lines = "\n".join(f"- {c}" for c in self.forbidden_claims)
            blocks.append(
                f"**FORBIDDEN claims (must NOT appear anywhere in the paper):**\n{lines}"
            )

        if self.venue_target:
            blocks.append(f"**Target venue:** {self.venue_target}")

        if self.output_sections:
            lines = []
            for s in self.output_sections:
                line = f"- {s.title}"
                if s.target_words:
                    line += f" (~{s.target_words} words)"
                if s.required_subsections:
                    line += f" | subsections: {'; '.join(s.required_subsections)}"
                if s.writing_style_hint:
                    line += f" | style: {s.writing_style_hint}"
                if s.content_notes:
                    line += f" | notes: {s.content_notes}"
                lines.append(line)
            blocks.append(
                f"**Required outline sections (in this exact order):**\n" + "\n".join(lines)
            )

        if self.additional_context:
            blocks.append(f"**Additional context from the author:**\n{self.additional_context}")

        if not blocks:
            return ""

        header = "RESEARCH BRIEF (author-provided — treat as authoritative, do not override or reinterpret)"
        return header + "\n\n" + "\n\n".join(blocks)
