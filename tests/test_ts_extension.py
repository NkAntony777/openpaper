#!/usr/bin/env python3
"""Pytest wrapper for the TS extension smoke test (tests/ts_extension_smoke.mjs).

The .ts extension is the one production-critical surface Python tests cannot reach;
this runs the offline node smoke (mocked ExtensionAPI, real spawn of the venv python
with OPENDRAFT_BOOTSTRAP). Skipped when node, the venv, or the pi install is absent.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
NODE = shutil.which("node")
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
PI_DIR = Path(r"E:\npm-global\node_modules\@earendil-works\pi-coding-agent")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not NODE or not VENV_PY.exists() or not PI_DIR.is_dir(),
    reason="TS smoke needs Windows + node + repo venv + pi install",
)


def test_ts_extension_smoke():
    proc = subprocess.run(
        [NODE, str(REPO / "tests" / "ts_extension_smoke.mjs")],
        cwd=str(REPO), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, (
        f"smoke failed (exit {proc.returncode})\n"
        f"--- stdout ---\n{proc.stdout[-3000:]}\n--- stderr ---\n{proc.stderr[-2000:]}"
    )
