#!/usr/bin/env python3
"""
ABOUTME: MCP / OpenAI function-calling schema export
ABOUTME: Exposes opendraft's phases as agent-consumable tool definitions so any
ABOUTME: MCP-capable agent (Claude Desktop, Cursor, Hermes, OpenAI Agents) can
ABOUTME: orchestrate the pipeline phase-by-phase.

Usage:
    from schemas import get_mcp_tools_schema, get_openai_function_schema
    tools = get_mcp_tools_schema()          # MCP inputSchema format
    funcs  = get_openai_function_schema()   # OpenAI function-calling format
"""

from typing import Any, Dict, List

_BRIEF_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "description": (
        "Structured research brief. All fields optional; provided fields take "
        "priority over topic+blurb everywhere in the pipeline."
    ),
    "properties": {
        "title": {"type": "string", "description": "Paper title (overrides topic)"},
        "core_question": {"type": "string", "description": "One-sentence research question"},
        "research_questions": {"type": "array", "items": {"type": "string"}, "description": "RQ1, RQ2, ..."},
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "e.g. H1"},
                    "statement": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["statement"],
            },
        },
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}},
                "required": ["name"],
            },
        },
        "innovations": {"type": "array", "items": {"type": "string"}, "description": "Stated contributions"},
        "baselines": {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "object", "properties": {"name": {"type": "string"}, "category": {"type": "string"}, "description": {"type": "string"}}, "required": ["name"]},
                ],
            },
        },
        "ablation_dims": {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "object", "properties": {"name": {"type": "string"}, "dimension": {"type": "string"}, "purpose": {"type": "string"}}, "required": ["name"]},
                ],
            },
        },
        "metrics": {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "object", "properties": {"name": {"type": "string"}, "k_value": {"type": "integer"}, "priority": {"type": "string", "enum": ["primary", "secondary"]}, "description": {"type": "string"}}, "required": ["name"]},
                ],
            },
        },
        "split_strategy": {"type": "string", "description": "e.g. temporal / user / scene"},
        "ground_truth_protocol": {"type": "string", "description": "Where ground truth comes from"},
        "forbidden_claims": {"type": "array", "items": {"type": "string"}, "description": "Claims that must NOT appear in the paper"},
        "venue_target": {"type": "string", "description": "e.g. ICWSM / WWW / KDD"},
        "literature_search_questions": {"type": "array", "items": {"type": "string"}, "description": "Structured literature-search queries (used verbatim by the research phase)"},
        "output_sections": {
            "type": "array",
            "description": "Author-specified outline; overrides LLM-generated outline",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "role": {"type": "string", "enum": ["introduction", "literature_review", "methodology", "results", "discussion", "conclusion", "appendix"]},
                    "target_words": {"type": "integer"},
                    "required_subsections": {"type": "array", "items": {"type": "string"}},
                    "writing_style_hint": {"type": "string"},
                    "content_notes": {"type": "string"},
                },
                "required": ["title"],
            },
        },
        "additional_context": {"type": "string"},
    },
}

_PHASE_ENUM = {
    "type": "string",
    "enum": ["research", "structure", "citations", "compose", "validate", "compile"],
}


def _tools() -> List[Dict[str, Any]]:
    """Tool definitions in a neutral format; adapted by the two exporters below."""
    return [
        {
            "name": "opendraft_run_research",
            "description": (
                "Run the literature-research phase only: discovers citations via API search, "
                "summarizes papers, identifies research gaps. Returns a structured "
                "PhaseResult (citation counts, artifact paths) instead of a full paper."
            ),
            "input": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "language": {"type": "string", "default": "en"},
                    "blurb": {"type": "string", "description": "Optional free-text focus"},
                    "literature_search_questions": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Structured search queries; used verbatim (brief priority)",
                    },
                    "output_dir": {"type": "string", "description": "Working/output directory"},
                },
                "required": ["topic"],
            },
        },
        {
            "name": "opendraft_run_phase",
            "description": (
                "Run a single pipeline phase (research/structure/citations/compose/validate/compile) "
                "against an existing working directory. Reads checkpoint.json for resume; "
                "returns a PhaseResult; never raises on phase failure (status=FAILED)."
            ),
            "input": {
                "type": "object",
                "properties": {
                    "phase": _PHASE_ENUM,
                    "topic": {"type": "string", "description": "Required unless resuming from checkpoint"},
                    "output_dir": {"type": "string", "description": "Directory holding checkpoint.json + artifacts"},
                    "research_brief": _BRIEF_SCHEMA,
                    "resume": {"type": "boolean", "default": True},
                    "save_checkpoint": {"type": "boolean", "default": True},
                },
                "required": ["phase", "output_dir"],
            },
        },
        {
            "name": "opendraft_get_pipeline_status",
            "description": (
                "Inspect a pipeline's progress from its checkpoint.json without running anything. "
                "Returns completed phases and the next phase to run."
            ),
            "input": {
                "type": "object",
                "properties": {
                    "checkpoint_path": {"type": "string", "description": "Path to checkpoint.json"},
                },
                "required": ["checkpoint_path"],
            },
        },
        {
            "name": "opendraft_generate_draft",
            "description": (
                "Generate a complete academic draft end-to-end (all phases). Supports a structured "
                "ResearchBrief for expert users with a full research plan, plus dry_run planning mode."
            ),
            "input": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "research_brief": _BRIEF_SCHEMA,
                    "brief_path": {"type": "string", "description": "Path to a brief .yaml/.json file"},
                    "language": {"type": "string", "default": "en"},
                    "academic_level": {"type": "string", "enum": ["research_paper", "bachelor", "master", "phd"], "default": "master"},
                    "citation_style": {"type": "string", "enum": ["apa", "ieee"], "default": "apa"},
                    "output_type": {"type": "string", "enum": ["full", "expose"], "default": "full"},
                    "venue_target": {"type": "string"},
                    "output_dir": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": False, "description": "Plan only; returns PhaseResult list without LLM calls"},
                    "headless": {"type": "boolean", "default": False},
                    "events_path": {"type": "string", "description": "Write a JSONL event stream to this path"},
                    "resume_from": {"type": "string", "description": "checkpoint.json path to resume from"},
                },
                "required": ["topic"],
            },
        },
        {
            "name": "opendraft_list_venues",
            "description": "List available venue prompt templates (e.g. icwsm, www).",
            "input": {"type": "object", "properties": {}},
        },
    ]


def get_mcp_tools_schema() -> List[Dict[str, Any]]:
    """Tool definitions in MCP format (name/description/inputSchema)."""
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["input"]}
        for t in _tools()
    ]


def get_openai_function_schema() -> List[Dict[str, Any]]:
    """Tool definitions in OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input"],
            },
        }
        for t in _tools()
    ]
