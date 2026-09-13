#!/usr/bin/env python3
"""
ABOUTME: T4 verify_claims — web-grounded fact checking. Thin wrapper over
ABOUTME: FactCheckVerifier (Gemini grounded search evidence + judge LLM). Read-only and
ABOUTME: parallelizable; verdicts carry wrong_part/correct_value pairs that feed
ABOUTME: revise_section's find_replace for structured fixes.
"""

import contextlib
import io
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

import requests
from agent_tools import registry
from agent_tools.envelope import fail, ok

PROBE_URL = "https://generativelanguage.googleapis.com/"
PROBE_TIMEOUT = 5


@contextlib.contextmanager
def _quiet_stdout():
    """Keep stdout JSON-envelope-clean: redirect prints and detach stdout log handlers."""
    root_logger = logging.getLogger()
    stdout_handlers = [
        h
        for h in list(root_logger.handlers)
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
    "Fact-check draft claims against live web evidence. Each claim gets a verdict: "
    "SUPPORTED, CONTRADICTED, or INSUFFICIENT, plus confidence, an evidence snippet and — "
    "for CONTRADICTED claims — a wrong_part/correct_value pair you can feed straight into "
    "revise_section's find_replace (also returned as data.find_replace). Read-only: "
    "verification results are NOT persisted. Requires GOOGLE_API_KEY (or GEMINI_API_KEY). "
    "Network failures are retryable."
)

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "minLength": 1},
                    "section": {"type": "string"},
                    "line": {"type": "string"},
                },
                "required": ["claim"],
            },
            "description": "Claims to verify (each must have a non-empty 'claim'; "
            "'section'/'line' are optional labels echoed back).",
        },
        "max_workers": {
            "type": "integer",
            "default": 10,
            "description": "Max parallel verification threads (default 10).",
        },
    },
    "required": ["claims"],
}


def verdicts_to_find_replace(verdicts: List[Dict]) -> List[Dict]:
    """T4→T6 glue: CONTRADICTED verdicts with a wrong_part/correct_value pair become
    revise_section find_replace entries. Verdicts missing either side are skipped."""
    pairs: List[Dict] = []
    for v in verdicts or []:
        if not isinstance(v, dict) or v.get("verdict") != "CONTRADICTED":
            continue
        find = v.get("wrong_part")
        replace = v.get("correct_value")
        if not isinstance(find, str) or not find.strip():
            continue
        if not isinstance(replace, str):
            continue
        pairs.append(
            {
                "find": find,
                "replace": replace,
                "claim": v.get("claim") or "",
            }
        )
    return pairs


def _probe_network() -> Optional[str]:
    """Return an error string if the Google API is unreachable, else None."""
    try:
        requests.get(PROBE_URL, timeout=PROBE_TIMEOUT)
        return None
    except requests.exceptions.RequestException as e:
        return f"network unreachable ({type(e).__name__}: {e}) — check connectivity and retry"


def run(args: Dict, root: Path) -> Dict:
    claims = args.get("claims")
    if not isinstance(claims, list) or not claims:
        return fail("claims must be a non-empty array of {claim, section?, line?} objects")

    normalized: List[Dict] = []
    for i, item in enumerate(claims):
        if not isinstance(item, dict):
            return fail(f"claims[{i}] must be an object with a 'claim' field")
        claim_text = item.get("claim")
        if not isinstance(claim_text, str) or not claim_text.strip():
            return fail(f"claims[{i}] is missing a non-empty 'claim' field")
        normalized.append(
            {
                "claim": claim_text.strip(),
                "section": item.get("section", ""),
                "line": item.get("line", ""),
            }
        )

    try:
        max_workers = int(args.get("max_workers") or 10)
    except (TypeError, ValueError):
        return fail("max_workers must be an integer")
    max_workers = max(1, min(max_workers, 20))

    from config import get_config

    config = get_config()
    api_key = config.google_api_key
    if not api_key:
        return fail(
            "GOOGLE_API_KEY (or GEMINI_API_KEY) is not set. Fact-checking is hard-wired to "
            "Google grounded search and needs this key even when the writing model uses a "
            "different provider. Set the env var and retry.",
            is_retryable=False,
            missing_env="GOOGLE_API_KEY",
        )

    net_error = _probe_network()
    if net_error:
        return fail(f"verify_claims failed: {net_error}", is_retryable=True)

    try:
        from utils.llm_runtime import setup_model

        model = setup_model()
    except Exception as e:
        return fail(f"could not set up judge model: {type(e).__name__}: {e}", is_retryable=False)

    try:
        from utils.factcheck_verifier import FactCheckVerifier

        with _quiet_stdout():
            verifier = FactCheckVerifier(api_key=api_key, model=model)
            verdicts = verifier.verify_claims(normalized, max_workers=max_workers)
    except Exception as e:
        return fail(
            f"fact-check failed: {type(e).__name__}: {e}",
            is_retryable=True,
        )

    return ok(
        {
            "verdicts": verdicts,
            "count": len(verdicts),
            "contradicted": sum(1 for v in verdicts if v.get("verdict") == "CONTRADICTED"),
            "find_replace": verdicts_to_find_replace(verdicts),
        }
    )


registry.register(
    registry.ToolSpec(
        name="verify_claims",
        description=DESCRIPTION,
        input_schema=INPUT_SCHEMA,
        func=run,
    )
)
