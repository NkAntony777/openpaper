#!/usr/bin/env python3
"""
ABOUTME: `opendraft harness` — the agent-harness driver CLI (section / review /
ABOUTME: paper / distill / eval). Progress goes to stderr; stdout carries
ABOUTME: exactly one JSON envelope line. Exit codes 0 ok / 1 failed / 2 usage.
"""

import json
import sys
from pathlib import Path


def run_harness_command(argv):
    """Agent harness driver: `opendraft harness section|review|paper --root DIR ...`.

    Machine-facing: progress goes to stderr; stdout carries exactly one JSON envelope line
    with the DriverResult summary. Exit codes: 0 ok, 1 run failed, 2 usage error.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="opendraft harness",
        description="Drive one paper-writing session via the pi agent harness.",
    )
    sub = parser.add_subparsers(dest="harness_cmd", metavar="<command>")
    p_section = sub.add_parser("section", help="Write one section with the pi agent loop")
    p_section.add_argument(
        "--root", type=Path, required=True, help="Paper output directory (the agent's working root)"
    )
    p_section.add_argument(
        "--section", required=True, help="Section to write, e.g. literature_review"
    )
    p_review = sub.add_parser("review", help="Global cross-section review with the pi agent loop")
    p_review.add_argument(
        "--root", type=Path, required=True, help="Paper output directory (the agent's working root)"
    )
    p_paper = sub.add_parser(
        "paper", help="Orchestrate a full paper: sections -> review -> fixes -> full score"
    )
    p_paper.add_argument(
        "--root", type=Path, required=True, help="Paper output directory (the agent's working root)"
    )
    p_paper.add_argument(
        "--sections",
        default=None,
        help="Comma-separated section list overriding the default plan "
        "(e.g. introduction,literature_review)",
    )
    p_paper.add_argument(
        "--compile", action="store_true", help="Compile/export the draft after the full score"
    )
    for p in (p_section, p_review):
        p.add_argument(
            "--model",
            default=None,
            help="pi model pattern (default: env PI_MODEL or minimax-cn/MiniMax-M3)",
        )
        p.add_argument(
            "--max-cost",
            type=float,
            default=1.0,
            help="Budget in USD before steering wrap-up (default 1.0)",
        )
        p.add_argument(
            "--max-turns",
            type=int,
            default=40,
            help="Max agent turns before steering wrap-up (default 40)",
        )
    p_paper.add_argument(
        "--model",
        default=None,
        help="pi model pattern (default: env PI_MODEL or minimax-cn/MiniMax-M3)",
    )
    p_paper.add_argument(
        "--max-cost",
        type=float,
        default=1.5,
        help="Total budget in USD across all paper sessions (default 1.5)",
    )
    p_paper.add_argument(
        "--max-turns",
        type=int,
        default=40,
        help="Max agent turns per session before steering wrap-up (default 40)",
    )
    p_paper.add_argument(
        "--min-score",
        type=int,
        default=75,
        help="Full-paper quality floor for the finish gate (default 75; "
        "the full score is computed after fixes, before the gate)",
    )
    p_distill = sub.add_parser(
        "distill", help="Distill run journal + status ledger into proposed lessons (offline)"
    )
    p_distill.add_argument(
        "--root", type=Path, required=True, help="Paper output directory (the agent's working root)"
    )

    p_eval = sub.add_parser(
        "eval", help="Offline eval-suite metrics for a paper directory (quality, claims, cites)"
    )
    p_eval.add_argument(
        "--root", type=Path, required=True, help="Paper output directory (the agent's working root)"
    )

    args = parser.parse_args(argv)
    if args.harness_cmd not in ("section", "review", "paper", "distill", "eval"):
        parser.print_help()
        return 2

    def _progress(msg):
        print(f"[harness] {msg}", file=sys.stderr, flush=True)

    sys.path.insert(0, str(Path(__file__).parent.parent))

    if args.harness_cmd == "distill":
        try:
            from harness.journal_distill import distill

            summary = distill(args.root)
        except Exception as e:
            _progress(f"error: {type(e).__name__}: {e}")
            payload = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            print(json.dumps(payload, ensure_ascii=False))
            return 1
        _progress(f"distilled {summary['proposed']} lesson(s) -> {summary['lessons_path']}")
        payload = {
            "ok": True,
            "data": {
                "proposed": summary["proposed"],
                "lessons_path": summary["lessons_path"],
            },
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    if args.harness_cmd == "eval":
        try:
            from harness.eval_suite import evaluate_root

            metrics = evaluate_root(args.root)
        except Exception as e:
            _progress(f"error: {type(e).__name__}: {e}")
            payload = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            print(json.dumps(payload, ensure_ascii=False))
            return 1
        _progress(
            f"eval: quality={metrics.quality_score} clean={metrics.factcheck_clean} "
            f"cites={metrics.citation_rate:.2f} passed={metrics.passed}"
        )
        payload = {"ok": metrics.passed, "data": metrics.to_dict()}
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if metrics.passed else 1

    if args.harness_cmd == "paper":
        from harness.paper_task import PaperBudget, run_paper

        sections = None
        if args.sections:
            sections = [s.strip() for s in args.sections.split(",") if s.strip()]
        result = run_paper(
            args.root,
            sections=sections,
            model=args.model,
            max_turns=args.max_turns,
            budget=PaperBudget(total_cost=args.max_cost),
            compile_at_end=args.compile,
            min_full_score=args.min_score,
            progress=_progress,
        )
        payload = {"ok": result.ok, "data": result.to_dict()}
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if result.ok else 1

    from harness.driver import BudgetConfig, PiDriver

    if args.harness_cmd == "section":
        from agent_tools.common import SECTION_FILES

        if args.section not in SECTION_FILES:
            _progress(
                f"error: unknown section '{args.section}' "
                f"(valid: {', '.join(sorted(SECTION_FILES))})"
            )
            return 2
        from harness.section_task import build_section_prompt

        prompt = build_section_prompt(args.root, args.section)
        session_name = f"section-{args.section}"
    else:
        from harness.review_task import build_review_prompt

        prompt = build_review_prompt(args.root)
        session_name = "global-review"

    try:
        driver = PiDriver(
            root=args.root,
            model=args.model,
            budget=BudgetConfig(max_cost_usd=args.max_cost, max_turns=args.max_turns),
        )
        _progress(f"root={driver.root} model={driver.model} pi={driver.pi_bin}")
        _progress(f"budget: cost<=${args.max_cost} turns<={args.max_turns} — preparing …")
        driver.prepare()
        _progress(f"prompt ready ({len(prompt)} chars); starting pi session '{session_name}' …")
        result = driver.run(prompt, name=session_name)
    except Exception as e:
        _progress(f"error: {type(e).__name__}: {e}")
        payload = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        print(json.dumps(payload, ensure_ascii=False))
        return 1

    _progress(
        f"done: ok={result.ok} reason={result.reason} budget_exceeded={result.budget_exceeded}"
    )

    acceptance = None
    global_issues_path = None
    if result.ok and args.harness_cmd == "section":
        # Final acceptance (design §5.2/T8): the model may finish without self-checking
        # (the PoC agent verified by hand with grep instead of score_draft). Re-sync the
        # section file into the checkpoint — the model can edit files with pi's native
        # write tool, which bypasses write_section's checkpoint sync — then score.
        try:
            from agent_tools.common import sync_checkpoint_section
            from agent_tools.score import run as score_run

            section_file = driver.root / SECTION_FILES[args.section]["file"]
            if section_file.exists():
                sync_checkpoint_section(
                    driver.root, args.section, section_file.read_text(encoding="utf-8")
                )
            verdict = score_run({"scope": "section", "section": args.section}, driver.root)
            acceptance = (
                verdict.get("data")
                if verdict.get("ok")
                else {
                    "passed": False,
                    "error": verdict.get("error"),
                }
            )
            from harness.acceptance import run_finish_acceptance

            gate = run_finish_acceptance(driver.root)
            acceptance = dict(acceptance or {})
            acceptance["claims_clean"] = gate.claims_clean
            acceptance["forbidden_hits"] = len(gate.forbidden_hits)
            acceptance["citation_rate"] = gate.citation_rate
            acceptance["finish_gaps"] = gate.gaps
            acceptance["finish_passed"] = gate.passed
            _progress(
                f"acceptance: passed={acceptance.get('passed')} "
                f"words={acceptance.get('words')} citations={len(acceptance.get('citations') or [])} "
                f"finish={'pass' if gate.passed else 'FAIL'}"
            )
            if gate.gaps:
                for g in gate.gaps:
                    _progress(f"finish gap: {g}")
        except Exception as e:
            _progress(f"acceptance check failed: {type(e).__name__}: {e}")

    if result.ok and args.harness_cmd == "review":
        # The deliverable is the settled markdown issue list — persist it next to the draft.
        settled = (result.settled_text or "").strip()
        if not settled:
            _progress("error: review settled but produced no text")
            payload = {"ok": False, "error": "review settled but produced no text"}
            print(json.dumps(payload, ensure_ascii=False))
            return 1
        out = driver.root / "global_issues.md"
        out.write_text(settled, encoding="utf-8")
        global_issues_path = str(out)
        issue_count = len([ln for ln in settled.splitlines() if ln.startswith("## GI-")])
        _progress(f"global issues: {issue_count} -> {out}")

    payload = {
        "ok": result.ok,
        "data": {
            "reason": result.reason,
            "budget_exceeded": result.budget_exceeded,
            "stats": result.stats,
            "journal_path": result.journal_path,
            "settled_text": result.settled_text,
            "acceptance": acceptance,
            "global_issues_path": global_issues_path,
        },
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if result.ok else 1
