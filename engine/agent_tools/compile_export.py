#!/usr/bin/env python3
"""
ABOUTME: T7 compile_draft — final assembly + export. Rebuilds DraftContext from
ABOUTME: checkpoint.json, re-runs the pipeline's compile phase (citation compilation with
ABOUTME: {cite_MISSING} auto-research, LLM abstract, pandoc PDF/DOCX export) and reports the
ABOUTME: paths that actually exist under exports/ (root-relative).
"""

import contextlib
import io
import logging
import sys
from pathlib import Path
from typing import Dict

from agent_tools import registry
from agent_tools.common import BIBLIOGRAPHY_REL, CHECKPOINT_NAME
from agent_tools.envelope import ok, fail
from phases.context import DraftContext

BIBLIOGRAPHY_NAME = BIBLIOGRAPHY_REL.split("/")[-1]

FORMATS = ("md", "pdf", "docx", "all")


@contextlib.contextmanager
def _quiet_stdout():
    """Keep stdout JSON-envelope-clean while running noisy pipeline code.

    Redirects process stdout (catches prints and import-time banners, e.g. weasyprint)
    and detaches root-logger handlers bound to stdout (pipeline modules log to stdout
    once configured). File logging keeps working; stderr is untouched.
    """
    root_logger = logging.getLogger()
    stdout_handlers = [
        h for h in list(root_logger.handlers)
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout
    ]
    for h in stdout_handlers:
        root_logger.removeHandler(h)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            yield
    finally:
        for h in stdout_handlers:
            root_logger.addHandler(h)

DESCRIPTION = (
    "Compile and export the finished draft. Prerequisites: a pipeline-produced "
    "checkpoint.json in the output root (all sections written), research/bibliography.json, "
    "a configured model (GOOGLE_API_KEY for Gemini or OPENAI_API_KEY/OPENAI_BASE_URL per "
    "AI_PROVIDER — used for the LLM abstract and {cite_MISSING} citation research) and "
    "pandoc for PDF export. Assembles intro/body/conclusion/appendices, compiles {cite_XXX} "
    "tokens into formatted citations, auto-researches {cite_MISSING:...} placeholders (new "
    "sources are appended to the bibliography), generates the abstract, and exports "
    "md/pdf/docx/zip into exports/. Deterministic given the same inputs (exports/ is "
    "overwritten). Any failure is reported with is_retryable=False — fix the underlying "
    "issue and re-run."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {
            "type": "string",
            "enum": list(FORMATS),
            "default": "all",
            "description": "Which export path(s) to report: 'md', 'pdf', 'docx' or 'all' "
                           "(default). The full compile always runs; this only filters the "
                           "reported outputs.",
        },
    },
}


def _rel(root: Path, path: Path) -> str:
    """Root-relative posix path for reporting (absolute fallback if outside root)."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


def run(args: Dict, root: Path) -> Dict:
    fmt = args.get("format") or "all"
    if fmt not in FORMATS:
        return fail(f"unknown format: {fmt} (valid: {list(FORMATS)})")

    root = Path(root)
    checkpoint_path = root / CHECKPOINT_NAME
    if not checkpoint_path.exists():
        return fail(
            f"{CHECKPOINT_NAME} not found in {root} — compile_draft needs pipeline "
            f"artifacts (run the pipeline or write all sections first)",
            is_retryable=False,
        )

    try:
        from utils.checkpoint import load_checkpoint, restore_context

        checkpoint_data, completed_phase = load_checkpoint(checkpoint_path)
    except FileNotFoundError as e:
        return fail(str(e), is_retryable=False)
    except Exception as e:
        return fail(f"cannot read {CHECKPOINT_NAME}: {type(e).__name__}: {e}", is_retryable=False)

    try:
        from config import get_config
        from utils.agent_runner import setup_model
        from utils.citation_database import load_citation_database
        from phases.compile import run_compile_and_export

        with _quiet_stdout():
            ctx = DraftContext()
            restore_context(ctx, checkpoint_data)
            ctx.verbose = False  # stdout is the JSON envelope contract — no pipeline prints
            ctx.config = get_config()
            # Real model: required so CitationCompiler can research {cite_MISSING} placeholders
            ctx.model = setup_model()

            bib_path = Path(ctx.folders.get("research", root / "research")) / BIBLIOGRAPHY_NAME
            if not bib_path.exists():
                bib_path = root / BIBLIOGRAPHY_REL
            if not bib_path.exists():
                return fail(
                    f"{BIBLIOGRAPHY_REL} not found — run the research phases first",
                    is_retryable=False,
                )
            ctx.citation_database = load_citation_database(bib_path)

            # The pipeline normally pre-creates the folder tree; a restored checkpoint
            # may point at an exports/ dir that does not exist yet.
            Path(ctx.folders["exports"]).mkdir(parents=True, exist_ok=True)

            pdf_path, docx_path = run_compile_and_export(ctx)

        exports_dir = Path(ctx.folders["exports"])
        base = Path(pdf_path).stem
        candidates = {
            "md": exports_dir / f"{base}.md",
            "pdf": Path(pdf_path),
            "docx": Path(docx_path),
            "zip": exports_dir / f"{base}.zip",
        }
        found = {k: _rel(root, p) for k, p in candidates.items() if p.exists()}
        if fmt != "all":
            found = {k: v for k, v in found.items() if k == fmt}
        if not found:
            return fail(
                f"compile finished but no {fmt} export exists under {exports_dir}",
                is_retryable=False,
            )

        return ok({
            "format": fmt,
            "exports": found,
            "completed_phase": completed_phase,
        })
    except Exception as e:
        return fail(f"compile/export failed: {type(e).__name__}: {e}", is_retryable=False)


registry.register(registry.ToolSpec(
    name="compile_draft",
    description=DESCRIPTION,
    input_schema=INPUT_SCHEMA,
    func=run,
))
