#!/usr/bin/env python3
"""Pytest wrapper for the TS extension smoke test (tests/ts_extension_smoke.mjs).

The .ts extension is the one production-critical surface Python tests cannot reach;
this runs the offline node smoke (mocked ExtensionAPI, real spawn of a python
binary with OPENDRAFT_BOOTSTRAP), then — via OD_TS_SCHEMA_DUMP — compares every
tool's TypeBox schema against the Python INPUT_SCHEMA so the two definitions
cannot drift apart silently.

Environment:
  PI_NODE_MODULES  directory containing @earendil-works/pi-coding-agent
                   (defaults to E:\\npm-global\\node_modules — the author's box;
                   CI installs a scratch copy and points this at it)
  OD_PY            python binary the envelope tests spawn (default: repo venv)

Skipped when node, a python binary, or the pi install is absent — which also
skips the parity test (it needs the dump the smoke produces).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
ENGINE = REPO / "engine"
NODE = shutil.which("node")

DEFAULT_PI_NM = r"E:\npm-global\node_modules"
PI_NM = Path(os.environ.get("PI_NODE_MODULES", DEFAULT_PI_NM))
PI_DIR = PI_NM / "@earendil-works" / "pi-coding-agent"


def _venv_python():
    for candidate in (
        REPO / ".venv" / "Scripts" / "python.exe",
        REPO / ".venv" / "bin" / "python",
    ):
        if candidate.exists():
            return candidate
    return None


def _env_ready():
    return (
        NODE is not None
        and PI_DIR.is_dir()
        and (_venv_python() is not None or os.environ.get("OD_PY"))
    )


pytestmark = pytest.mark.skipif(
    not _env_ready(), reason="TS smoke needs node + pi install + a python binary"
)

_smoke_dump = None


def _run_smoke():
    """Run the node smoke once with schema dumping; cache the parsed dump."""
    global _smoke_dump
    if _smoke_dump is None:
        fd, dump_path = tempfile.mkstemp(suffix=".json", prefix="od-ts-schema-")
        os.close(fd)
        env = dict(os.environ, OD_TS_SCHEMA_DUMP=dump_path)
        proc = subprocess.run(
            [NODE, str(REPO / "tests" / "ts_extension_smoke.mjs")],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        assert proc.returncode == 0, (
            f"smoke failed (exit {proc.returncode})\n"
            f"--- stdout ---\n{proc.stdout[-3000:]}\n--- stderr ---\n{proc.stderr[-2000:]}"
        )
        _smoke_dump = json.loads(Path(dump_path).read_text(encoding="utf-8"))
        Path(dump_path).unlink(missing_ok=True)
    return _smoke_dump


def test_ts_extension_smoke():
    _run_smoke()


# --------------------------------------------------------------- schema parity


def _ts_type_of(prop):
    """TypeBox encodes kinds via [Kind] symbols that don't survive JSON; the
    plain `type` field carries the JSON-Schema type (integer/string/array/...)."""
    if "enum" in prop:
        return "enum"
    return prop.get("type")


def _check_prop(name, tool, py, ts, problems):
    py_kind = "enum" if "enum" in py else py.get("type")
    ts_kind = _ts_type_of(ts)
    if py_kind != ts_kind:
        problems.append(f"{tool}.{name}: python kind {py_kind!r} != ts kind {ts_kind!r}")
        return
    if py_kind == "enum":
        if sorted(map(str, py["enum"])) != sorted(map(str, ts["enum"])):
            problems.append(f"{tool}.{name}: enum drift python={py['enum']} ts={ts['enum']}")


def test_ts_python_schema_parity():
    dump = _run_smoke()

    sys.path.insert(0, str(ENGINE))
    from agent_tools import registry

    problems = []
    for name in sorted(registry.MODULE_BY_NAME):
        spec = registry.get_tool(name)
        py_schema = spec.input_schema
        ts_schema = dump.get(name)

        if not isinstance(ts_schema, dict):
            problems.append(f"{name}: no TS schema dumped")
            continue

        py_props = py_schema.get("properties") or {}
        ts_props = ts_schema.get("properties") or {}
        py_only = sorted(set(py_props) - set(ts_props))
        ts_only = sorted(set(ts_props) - set(py_props))
        if py_only:
            problems.append(f"{name}: properties only in Python schema: {py_only}")
        if ts_only:
            problems.append(f"{name}: properties only in TS schema: {ts_only}")

        for prop in sorted(set(py_props) & set(ts_props)):
            _check_prop(prop, name, py_props[prop], ts_props[prop], problems)

        py_req = set(py_schema.get("required") or [])
        ts_req = set(ts_schema.get("required") or [])
        if py_req != ts_req:
            problems.append(f"{name}: required drift python={sorted(py_req)} ts={sorted(ts_req)}")

    assert not problems, "TS/Python tool schema drift:\n" + "\n".join(problems)
