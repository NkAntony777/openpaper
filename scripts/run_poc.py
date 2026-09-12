import json, shutil, subprocess, sys, tempfile, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).parent
FIXTURE = REPO / "tests" / "fixtures" / "poc_output"
PY = REPO / ".venv" / "Scripts" / "python.exe"

scratch = Path(tempfile.mkdtemp(prefix="opendraft_poc_"))
shutil.copytree(FIXTURE, scratch / "out")
root = scratch / "out"
print("PoC root:", root, flush=True)

cmd = [str(PY), "-c",
       "import sys; sys.path.insert(0, r'%s'); from opendraft.cli import run_harness_command; "
       "sys.exit(run_harness_command(['section', '--root', r'%s', '--section', 'literature_review', "
       "'--max-cost', '1.5', '--max-turns', '30']))" % (REPO / "engine", root)]
t0 = time.time()
r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=1800, cwd=REPO)
print("rc:", r.returncode, "elapsed:", round(time.time() - t0, 1), "s", flush=True)
print("stderr tail:", r.stderr[-800:], flush=True)
out_line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "{}"
try:
    env = json.loads(out_line)
except json.JSONDecodeError:
    print("non-JSON stdout tail:", r.stdout[-500:]); sys.exit(1)
print("envelope:", json.dumps(env, ensure_ascii=False)[:600], flush=True)

# ---- acceptance checks ----
sec = root / "drafts" / "02_1_literature_review.md"
text = sec.read_text(encoding="utf-8") if sec.exists() else ""
words = len(text.split())
import re
cites = sorted({f"cite_{n}" for n in re.findall(r"\{cite_(\d+)\}", text)})

sys.path.insert(0, str(REPO / "engine"))
from agent_tools.score import run as score_run
sc = score_run({"scope": "section", "section": "literature_review"}, root)

checks = {
    "section written (>=840 words)": words >= 840,
    "score_draft section passed": sc.get("ok") and sc["data"]["passed"],
    "citations used >= 8": len(cites) >= 8,
    "no error issues": sc.get("ok") and not any(i["severity"] == "error" for i in sc["data"]["issues"]),
    "driver ok": env.get("ok") is True,
}
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k, flush=True)
print(f"words={words} unique_cites={len(cites)} cost={env.get('data', {}).get('stats', {}).get('cost')}", flush=True)

journal = root / "run_journal.jsonl"
if journal.exists():
    tools = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "tool_execution_start":
            tools.append(ev.get("summary", "").split(" ")[0])
    print("tool sequence:", tools, flush=True)

print("POC:", "PASS" if all(checks.values()) else "FAIL", flush=True)
