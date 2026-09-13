# OpenPaper

[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Quality Gates](https://github.com/NkAntony777/openpaper/actions/workflows/quality.yml/badge.svg)](https://github.com/NkAntony777/openpaper/actions/workflows/quality.yml)

> An agent-native academic writing harness, rebuilt around one idea: **the model is the orchestrator, tools are the capabilities, disk is the truth.**

OpenPaper turns paper writing into a tool-use loop: an agent (driven by [pi](https://github.com/earendil-works/pi) in RPC mode) reads research material, searches literature, writes sections, scores its own draft against structured quality gates, and revises — with guardrails that make hallucinated citations structurally impossible and a budget breaker that keeps runs bounded.

Want to drive the same workflow from **your own** harness with your subscription instead? See the sibling repo [openpaper-cli](https://github.com/NkAntony777/openpaper-cli).

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
| `manage_claims` | Persistent claims ledger: record / list / verify / resolve | resolve is evidence-checked against the draft on disk |
| `revise_section` | Targeted revision (exact `find_replace` or LLM pass) | same guardrails as `write_section`; invalidates the section's passed bit |
| `write_outline` | Non-linear structure control: rewrite/merge the outline mid-writing | merge=true does heading-keyed replacement; syncs checkpoint |
| `compile_draft` | Deterministic compile + PDF/DOCX export | auto-backfills `{cite_MISSING}` **into the reference list** |

## Quickstart

Requirements: Python 3.10+, a pi install (`npm i -g @earendil-works/pi-coding-agent` or the standalone), and a MiniMax key (`MINIMAX_API_KEY`) — the reference setup uses `MiniMax-M3` via pi's `minimax-cn` provider; other pi-supported providers work via `--model`/`PI_MODEL`.

```bash
# 1. Try the tool surface (single-line JSON envelope on stdout)
opendraft tool list
opendraft tool score_draft --root <output dir> --args '{"scope":"full"}'

# 2. Let the agent write one section end-to-end (given a research/ dir)
opendraft harness section --root <output dir> --section literature_review

# 3. Run the whole paper: sections → global review → targeted fixes → finish gate
opendraft harness paper --root <output dir>          # add --compile for PDF/DOCX

# 4. After a run: distill lessons from the journal, or score a directory offline
opendraft harness distill --root <output dir>
opendraft harness eval --root <output dir>
```

A ready-made research fixture for trying things out: `scripts/make_poc_fixture.py` builds one (bibliography + paper notes + outline + checkpoint) under `tests/fixtures/poc_output/`.

**Current scope, honestly**: research/structure are still a prepared directory (bring your own, or `scripts/make_poc_fixture.py`). Writing, review, claim-audit, and lesson distillation run as an agent harness (`opendraft harness paper` / `distill` / `eval`). Fully autonomous topic→paper research remains future work.

## Configuration

| Env | Meaning |
|---|---|
| `PI_MODEL` / `--model` | pi model pattern (default `minimax-cn/MiniMax-M3`) |
| `PI_BIN` | pi executable (default: resolved from `PATH`; override with an absolute path if needed) |
| `OPENAI_API_KEY` + `OPENAI_BASE_URL` | OpenAI-compatible backend for OpenDraft's own LLM calls (revise, abstract, verifier) |
| `MINIMAX_API_KEY` / `MINIMAX_CN_API_KEY` | pi's native MiniMax provider keys (the driver auto-maps `OPENAI_API_KEY`) |
| `GOOGLE_API_KEY` | needed only for `verify_claims` (Gemini-grounded search) |

## Testing

```bash
pytest tests/ -q        # 620+ offline tests; no network, no LLM calls
```

Every layer is testable without pi or a model: the driver's event loop takes injected `next_line`/`send`/`clock`, so budget escalation, UI-dialog handling, and journal behavior run against canned JSONL. The pi extension (`opendraft-tools.ts`) has its own offline smoke test (`tests/ts_extension_smoke.mjs`, 24 assertions: tool registration, spawn argv, envelope error model, `.cmd` rejection, compaction fallback) wired into pytest — plus a **schema-parity test** that compares every tool's TypeBox schema against the Python `INPUT_SCHEMA`, so the two definitions cannot drift silently. CI installs the pi packages and runs the TS smoke + parity **on Linux**, not only on the dev box. `ruff check` + `ruff format --check` gate every push.

## Roadmap

- **M2 (done)**: state ledgers for non-linear control — `section_status.json`, per-section summary ledger, `harness review` → `global_issues.md`, then `harness paper` orchestration
- **M3 (done)**: claims ledger (`manage_claims` record/verify/resolve), T4→T6 `find_replace` chain, `forbidden_claims` + CONTRADICTED finish gate, `write_outline`, paper-aware pi compaction
- **M4 (done)**: `opendraft harness distill` → `lessons_proposed.md` (human review) → `lessons/approved/` + `templates/lessons/` injected into `AGENTS.md`; offline eval suite (`tests/eval_gold/`, `opendraft harness eval`) in CI
- **M5 (done)**: third-party audit remediation — finish gate now checks section presence + word floors + a `--min-score` quality floor (negation-aware forbidden scan), fix sessions are re-scored on disk truth before being accepted, review failures fail the run, and cost telemetry is real (`spent=` per session)

**Known boundaries** (next candidates): pi's built-in file-write tools can still bypass tool guardrails (allowlist trade-off), review issue routing keys on section names in the fix text, and lesson injection is not yet topic-scoped.

See [docs/AGENT_HARNESS_DESIGN.md](docs/AGENT_HARNESS_DESIGN.md) for the full design (incl. the quality-gate audit that motivated the refactor).

## Credits & license

Continues the work of [OpenDraft](https://github.com/federicodeponte/opendraft) by Federico De Ponte (MIT); agent 底座 [pi](https://github.com/earendil-works/pi) by Mario Zechner / Earendil (MIT). MIT License.
