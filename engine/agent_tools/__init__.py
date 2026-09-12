#!/usr/bin/env python3
"""
ABOUTME: Agent tools for the paper-writing harness (M0 tool layer)
ABOUTME: Each tool is a pure function (args, root) -> envelope dict, exposed via
ABOUTME: `opendraft tool <name> --root <dir> --args '<json>'`. See docs/AGENT_HARNESS_DESIGN.md.
"""

from agent_tools.envelope import ok, fail, ToolInputError, resolve_under_root  # noqa: F401
from agent_tools.registry import ToolSpec, register, get_tool, list_tools  # noqa: F401
