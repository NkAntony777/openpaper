#!/usr/bin/env python3
"""
ABOUTME: `opendraft tool` — the machine-facing agent-tool CLI. Prints exactly
ABOUTME: one JSON envelope line on stdout; exit codes 0 ok / 1 tool failure /
ABOUTME: 2 usage error. Invoked by the pi extension via OPENDRAFT_BIN.
"""

import sys
from pathlib import Path


def run_tool_command(argv):
    """Agent tool interface: `opendraft tool <name> --root <dir> --args '<json>'`.

    Machine-facing: prints exactly one JSON envelope line on stdout.
    Exit codes: 0 ok, 1 tool-level failure (or partial errors on list), 2 usage error.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="opendraft tool",
        description="Agent tool interface. Prints one JSON envelope line on stdout.",
    )
    parser.add_argument("name", help="Tool name, or 'list' to list all tools")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Output directory the tool operates on (default: cwd)",
    )
    parser.add_argument(
        "--args", default="{}", help="Tool arguments as a JSON object string (default '{}')"
    )
    parser.add_argument(
        "--args-file", type=Path, help="Read tool arguments from a JSON file instead of --args"
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Print the tool's input JSON schema instead of running it",
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).parent.parent))

    def _emit(payload, code):
        # UTF-8 bytes to stdout regardless of console codepage — this is a machine contract.
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        except (AttributeError, OSError):
            print(json.dumps(payload, ensure_ascii=False))
        return code

    try:
        from agent_tools import registry
    except Exception as e:
        return _emit({"ok": False, "error": f"tool layer unavailable: {type(e).__name__}: {e}"}, 2)

    if args.name == "list":
        available, errors = registry.list_tools()
        return _emit(
            {"ok": True, "data": {"tools": available, "errors": errors}}, 0 if not errors else 1
        )

    try:
        spec = registry.get_tool(args.name)
    except KeyError as e:
        return _emit({"ok": False, "error": str(e)}, 2)
    except Exception as e:
        return _emit({"ok": False, "error": f"tool failed to load: {type(e).__name__}: {e}"}, 2)

    if args.schema:
        return _emit(
            {
                "ok": True,
                "data": {
                    "name": spec.name,
                    "description": spec.description,
                    "input_schema": spec.input_schema,
                },
            },
            0,
        )

    try:
        raw = args.args_file.read_text(encoding="utf-8") if args.args_file else args.args
    except OSError as e:
        return _emit({"ok": False, "error": f"cannot read args file: {e}"}, 2)
    try:
        tool_args = json.loads(raw)
    except json.JSONDecodeError as e:
        return _emit({"ok": False, "error": f"--args is not valid JSON: {e}"}, 2)
    if not isinstance(tool_args, dict):
        return _emit({"ok": False, "error": "--args must be a JSON object"}, 2)

    try:
        result = spec.func(tool_args, args.root)
    except Exception as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}", "is_retryable": False}
    return _emit(result, 0 if result.get("ok") else 1)
