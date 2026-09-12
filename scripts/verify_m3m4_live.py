#!/usr/bin/env python3
"""Live smoke test for the M3/M4 TS extension + .cmd spawn fix.

Builds a fresh PoC fixture, runs one tiny pi RPC session whose prompt forces a
real tool call (read_artifact), then greps run_journal.jsonl for the
tool_execution_start/end pair. Any extension load failure or spawn EINVAL
shows up as a missing/failed tool pair.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "engine"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from harness.driver import BudgetConfig, DriverResult, PiDriver  # noqa: E402


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="opendraft_m3m4_verify_"))
    root = tmp / "paper"
    shutil.copytree(REPO / "tests" / "fixtures" / "poc_output", root)
    print(f"[verify] root={root}", flush=True)

    driver = PiDriver(root, budget=BudgetConfig(max_cost_usd=0.05, max_turns=6))
    driver.prepare()

    prompt = (
        "Use the read_artifact tool to read research/combined_research.md. "
        "Then immediately stop and reply with the single word: DONE. "
        "Do not use any other tool."
    )
    result: DriverResult = driver.run(prompt, name="verify-m3m4")

    journal = (root / "run_journal.jsonl").read_text(encoding="utf-8").splitlines()
    starts, ends, ext_errors = [], [], []
    for raw in journal:
        try:
            e = json.loads(raw)
        except json.JSONDecodeError:
            continue
        summary = str(e.get("summary") or "")
        if e.get("type") == "tool_execution_start":
            starts.append(summary)
        elif e.get("type") == "tool_execution_end":
            ends.append(summary)
        elif e.get("type") in ("extension_error", "error"):
            ext_errors.append(summary)

    print(f"[verify] driver ok={result.ok} reason={result.reason}", flush=True)
    print(f"[verify] tool starts: {starts}", flush=True)
    print(f"[verify] tool ends:   {ends}", flush=True)
    print(f"[verify] ext errors:  {ext_errors}", flush=True)

    ok = (
        result.ok
        and any(s.startswith("read_artifact") for s in starts)
        and any("read_artifact ok" in e for e in ends)
        and not ext_errors
    )
    print(f"[verify] RESULT: {'PASS' if ok else 'FAIL'}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
