"""
OpenDraft UI runner: executes generate_draft with full parameter set.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from draft_generator import generate_draft


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenDraft UI runner")
    parser.add_argument("--params", required=True, help="Path to JSON params file")
    args = parser.parse_args()

    params_path = Path(args.params).resolve()
    params = json.loads(params_path.read_text(encoding="utf-8"))

    output_dir = Path(params.get("output_dir") or "tests/outputs/generated_draft")
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "ui_run.log"

    # Ensure local progress tracking
    os.environ["OPENDRAFT_LOCAL_PROGRESS"] = "1"
    os.environ["OPENDRAFT_LOCAL_PROGRESS_ID"] = params.get("run_id", "local")
    os.environ["OPENDRAFT_UI_LOG_PATH"] = str(log_path)

    generate_draft(
        topic=params["topic"],
        language=params.get("language", "en"),
        academic_level=params.get("academic_level", "master"),
        output_dir=output_dir,
        output_type=params.get("output_type", "full"),
        skip_validation=not params.get("validate", False),
        enforce_citation_gate=params.get("enforce_citation_gate"),
        enforce_quality_gate=params.get("enforce_quality_gate"),
        blurb=params.get("blurb") or None,
        citation_style=params.get("citation_style", "apa"),
        resume_from=Path(params["resume_from"]) if params.get("resume_from") else None,
        author_name=params.get("author_name") or None,
        institution=params.get("institution") or None,
        department=params.get("department") or None,
        faculty=params.get("faculty") or None,
        advisor=params.get("advisor") or None,
        second_examiner=params.get("second_examiner") or None,
        location=params.get("location") or None,
        student_id=params.get("student_id") or None,
        verbose=True,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
