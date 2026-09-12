#!/usr/bin/env python3
"""
ABOUTME: Tool registry with lazy per-tool loading.
ABOUTME: A broken tool fails only itself (get_tool raises), never the whole CLI.
"""

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Tuple

ToolFunc = Callable[[Dict, Path], Dict]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict
    func: ToolFunc


# tool name -> module that registers it (module self-registers on import)
MODULE_BY_NAME = {
    "read_artifact": "agent_tools.artifacts",
    "write_section": "agent_tools.write_section",
    "score_draft": "agent_tools.score",
    "search_literature": "agent_tools.literature",
    "verify_claims": "agent_tools.claims",
    "revise_section": "agent_tools.revise",
    "compile_draft": "agent_tools.compile_export",
    "write_outline": "agent_tools.outline",
    "manage_claims": "agent_tools.claims_ledger",
}

_registry: Dict[str, ToolSpec] = {}
_loaded = set()


def register(spec: ToolSpec) -> None:
    _registry[spec.name] = spec


def get_tool(name: str) -> ToolSpec:
    """Load (once) and return the tool spec. Raises KeyError for unknown names."""
    if name not in MODULE_BY_NAME:
        raise KeyError(f"unknown tool: {name}")
    if name not in _loaded:
        importlib.import_module(MODULE_BY_NAME[name])
        _loaded.add(name)
    return _registry[name]


def list_tools() -> Tuple[List[Dict], List[Dict]]:
    """Import every tool module. Returns (available, errors) — listing never crashes."""
    available, errors = [], []
    for name in MODULE_BY_NAME:
        try:
            spec = get_tool(name)
            available.append({"name": spec.name, "description": spec.description})
        except Exception as e:  # report, don't crash
            errors.append({"name": name, "error": f"{type(e).__name__}: {e}"})
    return sorted(available, key=lambda t: t["name"]), errors
