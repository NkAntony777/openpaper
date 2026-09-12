# OpenPaper

[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Built on OpenDraft](https://img.shields.io/badge/Built%20on-OpenDraft-orange)](https://github.com/federicodeponte/opendraft)

> An agent-native academic writing harness, built on [OpenDraft](https://github.com/federicodeponte/opendraft) and rebuilt around one idea: **the model is the orchestrator, tools are the capabilities, disk is the truth.**

OpenPaper turns paper writing into a tool-use loop: an agent (driven by [pi](https://github.com/earendil-works/pi) in RPC mode) reads research material, searches literature, writes sections, scores its own draft against structured quality gates, and revises — with guardrails that make hallucinated citations structurally impossible and a budget breaker that keeps runs bounded.

---

## What changed vs OpenDraft

OpenDraft runs a **fixed 19-agent pipeline** (research → structure → compose → QA → export). Its quality gates *report* problems into markdown files for humans; they never *act* on them — the backend "writes the draft and ships it". OpenPaper replaces the orchestration with an agent loop over a small set of solid tools, and closes the quality feedback loop:

| Area | OpenDraft | OpenPaper |
|---|---|---|
| Orchestration | Fixed phase pipeline, 19 agents, 7 phases | Agent loop + tools; the model decides the next step (evaluator-optimizer) |
| Quality feedback | Score/warning only — no rewrite path | Structured issues `{section, metric, actual, target, severity}` fed back to the model, which revises and re-scores until clean |
| Citation safety | LLM fallback disabled, but no write-time check | **Whitelist guardrail**: `write_section` rejects any `cite_XXX` not in `research/bibliography.json` — hallucinated citations can't reach disk |
| Tool surface | None for agents (phases only callable as coarse API stubs) | 9 composable tools behind a machine-readable `opendraft tool` CLI contract |
| Long-run safety | Checkpoint/resume per phase | + budget breaker (cost/turns/wall-clock → steer → abort → process-tree kill), append-only run journal |
| Writing context | ~3k-char prompt stuffing per section | `AGENTS.md` paper map + on-demand artifact reads (context is a cache; disk is the truth) |
| Headless operation | Interactive CLI + UIs | Unattended RPC driver: extension isolation, UI dialogs auto-cancelled, no shell by default |
| Codebase | 2.5k-line orchestrator god object + 19 prompts + 2 UIs + 36 utils | Tool layer + harness ≈ 2.7k lines total; ~96 pipeline files retired |

Bug fixes and refactors on top of OpenDraft's foundation:

- **Reference-list ordering bug**: `generate_reference_list` ran before `compile_citations`, so auto-researched `{cite_MISSING}` citations never reached the bibliography (new `CitationCompiler.generate_reference_list_for_ids`, regression-tested)
- **Quality gate refactor**: `score_draft_quality(ctx)` split into a pure `score_texts()` core emitting structured diagnostics; the old ctx-based API delegates unchanged
- Version aligned (CLI reported a stale version), packaging fixed (`harness*` included)

Everything OpenDraft built that matters is kept and reused: the citation API cascade (Semantic Scholar / Crossref / OpenAlex / Serper / Gemini-grounded), the citation database + compiler, the web-grounded fact-check verifier, the revise channel with circuit breaker, and the Pandoc export chain.

---

## Architecture

```
┌ Driver (engine/harness/driver.py) ──────────────────────────────┐
│ Python · spawns pi in RPC mode · budget breaker (cost/turns/time)│
│ steer → abort → taskkill-tree · run_journal.jsonl · acceptance   │
└──────────────┬───────────────────────────────────────────────────┘
               │ stdin/stdout JSONL
┌ pi agent (底座) ─────────────────────────────────────────────────┐
│ agent loop · message history · auto-compaction · tool execution  │
│ + opendraft-tools.ts extension (9 tools, TypeBox, guidelines)    │
└──────────────┬───────────────────────────────────────────────────┘
               │ `opendraft tool <name> --root DIR --args '<json>'`
┌ Tool layer (engine/agent_tools/) ────────────────────────────────┐
│ read_artifact · write_section · score_draft · search_literature  │
│ verify_claims · revise_section · compile_draft · write_outline   │
│ manage_claims (claims ledger: record/list/verify/resolve)        │
│ envelope: {"ok", "data|error", "is_retryable"} — errors are      │
│ tool results, never crashes                                      │
└──────────────┬───────────────────────────────────────────────────┘
               ▼
        <output root>/  ← 唯一事实源: research/ · drafts/ ·
        research/bibliography.json · checkpoint.json · AGENTS.md
```

## The tools

| Tool | What it does | Guardrails |
|---|---|---|
| `read_artifact` | Read research notes, outline, bibliography, drafts | path-confined to the output root, paged reads |
| `search_literature` | Search academic databases; results become citable immediately | LLM-fabricated citations stay disabled |
| `write_section` | Idempotent full-section write + checkpoint sync | citation whitelist, word floor, placeholder rejection, snapshots |
| `score_draft` | 100-point gate, **structured issues** as the fix backlog | read-only, idempotent |
| `verify_claims` | Web-grounded fact-check → `wrong_part`/`correct_value` pairs | — |
| `revise_section` | Targeted revision (exact `find_replace` or LLM pass) | same guardrails as `write_section` |
| `compile_draft` | Deterministic compile + PDF/DOCX export | auto-backfills `{cite_MISSING}` **into the reference list** |

## Quickstart

Requirements: Python 3.10+, a pi install (`npm i -g @earendil-works/pi-coding-agent` or the standalone), and a MiniMax key (`MINIMAX_API_KEY`) — the reference setup uses `MiniMax-M3` via pi's `minimax-cn` provider; other pi-supported providers work via `--model`/`PI_MODEL`.

```bash
# 1. Try the tool surface (single-line JSON envelope on stdout)
opendraft tool list
opendraft tool score_draft --root <output dir> --args '{"scope":"full"}'

# 2. Let the agent write one section end-to-end (given a research/ dir)
opendraft harness section --root <output dir> --section literature_review

# 3. Run the acceptance PoC (fixtures a research dir, agent writes the section)
python scripts/run_poc.py
```

A ready-made research fixture for trying things out: `scripts/make_poc_fixture.py` builds one (bibliography + paper notes + outline + checkpoint) under `tests/fixtures/poc_output/`.

**Current scope, honestly**: research/structure are still a prepared directory (bring your own, or `scripts/make_poc_fixture.py`). Writing, review, claim-audit, and lesson distillation run as an agent harness (`opendraft harness paper` / `distill` / `eval`). Fully autonomous topic→paper research remains future work.

## Configuration

| Env | Meaning |
|---|---|
| `PI_MODEL` / `--model` | pi model pattern (default `minimax-cn/MiniMax-M3`) |
| `PI_BIN` | pi executable (default `E:\npm-global\pi.cmd`; override anywhere else) |
| `OPENAI_API_KEY` + `OPENAI_BASE_URL` | OpenAI-compatible backend for OpenDraft's own LLM calls (revise, abstract, verifier) |
| `MINIMAX_API_KEY` / `MINIMAX_CN_API_KEY` | pi's native MiniMax provider keys (the driver auto-maps `OPENAI_API_KEY`) |
| `GOOGLE_API_KEY` | needed only for `verify_claims` (Gemini-grounded search) |

## Testing

```bash
pytest tests/ -q        # 500+ offline tests; no network, no LLM calls
```

Every layer is testable without pi or a model: the driver's event loop takes injected `next_line`/`send`/`clock`, so budget escalation, UI-dialog handling, and journal behavior run against canned JSONL.

## Roadmap

- **M2 (done)**: state ledgers for non-linear control — `section_status.json`, per-section summary ledger, `harness review` → `global_issues.md`, then `harness paper` orchestration
- **M3 (done)**: claims ledger (`manage_claims` record/verify/resolve), T4→T6 `find_replace` chain, `forbidden_claims` + CONTRADICTED finish gate, `write_outline`, paper-aware pi compaction
- **M4 (done)**: `opendraft harness distill` → `lessons_proposed.md` (human review) → `lessons/approved/` + `templates/lessons/` injected into `AGENTS.md`; offline eval suite (`tests/eval_gold/`, `opendraft harness eval`) in CI

See [docs/AGENT_HARNESS_DESIGN.md](docs/AGENT_HARNESS_DESIGN.md) for the full design (incl. the quality-gate audit that motivated the refactor).

## Credits & license

OpenPaper is built on [OpenDraft](https://github.com/federicodeponte/opendraft) by Federico De Ponte (MIT). The agent底座 is [pi](https://github.com/earendil-works/pi) by Mario Zechner / Earendil (MIT). MIT License.
