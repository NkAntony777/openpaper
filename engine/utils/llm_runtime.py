#!/usr/bin/env python3
"""
ABOUTME: LLM runtime primitives shared by the pipeline and the agent tools.
ABOUTME: Model construction (setup_model), prompt loading, and run_agent — the
ABOUTME: retrying/validating LLM call loop with transient-error backoff,
ABOUTME: empty-output detection, and partial-output salvage on timeout.

Split out of the former utils/agent_runner.py god object; the citation-research
half of that module lives in utils/citation_research.py.
"""

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import get_config
from utils.gemini_client import GeminiModelWrapper
from utils.openai_client import OpenAIModelWrapper
from utils.output_validators import ValidationResult
from utils.token_tracker import CallStatus

logger = logging.getLogger(__name__)


def safe_print(*args, **kwargs):
    """Print wrapper that catches BrokenPipeError and respects CLI quiet mode."""
    # Check verbosity setting from orchestrator (CLI quiet mode)
    try:
        from utils.api_citations.orchestrator import _verbose_research

        if not _verbose_research:
            return  # Suppress in CLI quiet mode
    except ImportError:
        pass  # If orchestrator not available, continue normally

    try:
        print(*args, **kwargs)
    except (BrokenPipeError, OSError):
        # Pipe is closed (worker running with stdio: 'ignore'), use logger instead
        message = " ".join(str(arg) for arg in args)
        logger.debug(message)
        # Prevent further broken pipe errors by redirecting stdout
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass


def setup_model(model_override: Optional[str] = None) -> Any:
    """
    Initialize and return configured model wrapper.

    Args:
        model_override: Optional model name to override config default

    Returns:
        Model wrapper with generate_content() method

    Raises:
        ValueError: If API key is missing or model name is invalid
    """
    config = get_config()

    model_name = model_override or config.model.model_name

    if config.model.provider == "openai":
        if not config.openai_api_key:
            raise ValueError("OPENAI_API_KEY required for OpenAI-compatible models")
        return OpenAIModelWrapper(
            model_name=model_name,
            api_key=config.openai_api_key,
            base_url=config.openai_base_url,
            temperature=config.model.temperature,
            max_tokens=config.model.max_output_tokens or 8192,
        )

    # Gemini provider (default)
    from google import genai

    if not config.google_api_key:
        raise ValueError("GOOGLE_API_KEY not found. Set it in .env file or environment variables.")

    client = genai.Client(api_key=config.google_api_key)
    return GeminiModelWrapper(
        client=client,
        model_name=model_name,
        temperature=config.model.temperature,
    )


def load_prompt(prompt_path: str) -> str:
    """
    Load agent prompt from markdown file.

    Args:
        prompt_path: Path to prompt file (relative to project root or absolute)

    Returns:
        str: Content of the prompt file

    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    config = get_config()
    path = Path(prompt_path)

    # If relative path, try relative to project root
    if not path.is_absolute():
        path = config.paths.project_root / path

    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_response_text(response: Any, name: str) -> str:
    """Pull the text out of a Gemini-style response, with precise diagnostics.

    Raises ValueError (agent name + finish reason in the message) when the
    response carries no usable text — function-call-only, blocked, or empty.
    """
    if not response.candidates:
        raise ValueError(f"Agent '{name}': No candidates in response (likely blocked)")

    candidate = response.candidates[0]
    finish_reason = getattr(candidate, "finish_reason", None)

    # Check what parts exist in the response BEFORE accessing response.text
    # This prevents ValueError when response has function calls or no text parts
    has_text_part = False
    has_function_call = False

    if hasattr(candidate, "content") and candidate.content:
        for part in candidate.content.parts:
            if hasattr(part, "text") and part.text:
                has_text_part = True
                break
            if hasattr(part, "function_call"):
                has_function_call = True

    output: Optional[str] = None

    if has_text_part:
        # Safe to use response.text when a text part exists
        try:
            output = str(response.text)
        except ValueError:
            # Fallback: extract from parts
            if hasattr(candidate, "content") and candidate.content:
                text_parts = [
                    part.text
                    for part in candidate.content.parts
                    if hasattr(part, "text") and part.text
                ]
                if text_parts:
                    output = "".join(text_parts)
    elif has_function_call:
        raise ValueError(
            f"Agent '{name}': Response contains function call but no text content. "
            f"Finish reason: {finish_reason}. "
            f"This may indicate the model attempted to call a function incorrectly. "
            f"Try regenerating or check if tools are properly configured."
        )
    else:
        if finish_reason == 1:  # STOP (normal) but no text - unusual
            raise ValueError(
                f"Agent '{name}': Response has finish_reason=1 (STOP) but no text parts. "
                f"This may indicate an empty response or safety filter block."
            )
        if finish_reason == 10:  # FUNCTION_CALL
            raise ValueError(
                f"Agent '{name}': Response has finish_reason=10 (FUNCTION_CALL) but no text content. "
                f"This indicates an invalid function call attempt."
            )
        raise ValueError(
            f"Agent '{name}': No text content in response. "
            f"Finish reason: {finish_reason}. "
            f"Response may be blocked or empty."
        )

    if not output:
        raise ValueError(f"Agent '{name}': Unable to extract text from response")
    return str(output)


def _capture_partial_output(save_path: Path, agent_name: str) -> Optional[str]:
    """
    Capture partial output on timeout (V3 feature).

    When an agent times out, check if any partial work was written to the output
    directory. This recovers work that would otherwise be lost.

    Args:
        save_path: Path where output would be saved
        agent_name: Name of the agent (for logging)

    Returns:
        str or None: Partial output if found, None otherwise
    """
    try:
        output_dir = save_path.parent
        if not output_dir.exists():
            return None

        # Check for the output file itself (might have been partially written)
        if save_path.exists() and save_path.stat().st_size > 0:
            content = save_path.read_text(encoding="utf-8")
            if len(content.strip()) > 50:  # Non-trivial content
                logger.info(f"Agent '{agent_name}': Found partial output in {save_path}")
                return content

        # Check for any recent files in the output directory
        recent_files = []
        for f in output_dir.iterdir():
            if f.is_file() and f.suffix in [".md", ".txt", ".json"]:
                recent_files.append((f, f.stat().st_mtime))

        if not recent_files:
            return None

        # Get most recently modified file
        recent_files.sort(key=lambda x: x[1], reverse=True)
        most_recent = recent_files[0][0]

        if most_recent.stat().st_size > 0:
            content = most_recent.read_text(encoding="utf-8")
            if len(content.strip()) > 50:
                logger.info(f"Agent '{agent_name}': Found partial output in {most_recent}")
                return content

        return None
    except Exception as e:
        logger.debug(f"Agent '{agent_name}': Partial output capture failed: {e}")
        return None


def _is_transient_error(error: Exception) -> bool:
    """
    Check if error is transient and worth retrying.
    Also signals backpressure system for rate limit errors.

    Args:
        error: Exception to check

    Returns:
        bool: True if error is transient (network, rate limit, etc.)
    """
    error_str = str(error).lower()
    # Expanded patterns from V3 - covers more network/API edge cases
    transient_patterns = [
        # Rate limiting
        "timeout",
        "rate limit",
        "rate_limit",
        "ratelimit",
        "quota",
        "throttl",
        "429",  # Too Many Requests
        # Server errors
        "service unavailable",
        "temporarily unavailable",
        "server error",
        "internal error",
        "500",  # Internal Server Error
        "502",  # Bad Gateway
        "503",  # Service Unavailable
        "504",  # Gateway Timeout
        # Network errors
        "connection reset",
        "connection refused",
        "connection closed",
        "server disconnected",
        "broken pipe",
        "network unreachable",
        "dns",
        "ssl",
        "certificate",
        "handshake",
        # API-specific
        "resource exhausted",
        "overloaded",
        "capacity",
        "retry",
        "try again",
        "temporary",
    ]

    is_transient = any(pattern in error_str for pattern in transient_patterns)

    # Signal backpressure for rate limit errors
    if is_transient and ("429" in error_str or "rate limit" in error_str or "quota" in error_str):
        try:
            from utils.backpressure import APIType, BackpressureManager

            bp = BackpressureManager()
            bp.signal_429(APIType.GEMINI_PRIMARY)
            logger.debug("Signaled backpressure for rate limit error")
        except Exception:
            pass  # Don't fail on backpressure errors

    return is_transient


def rate_limit_delay(seconds: Optional[float] = None) -> None:
    """
    Sleep for rate limiting with tier-adaptive delays.

    Automatically adjusts delay based on detected API tier:
    - Free tier (10 RPM): 7 seconds (safe for 1 req/6s limit)
    - Paid tier (2,000 RPM): 0.3 seconds (safe for high throughput)

    Args:
        seconds: Manual override (default: None = use tier-adaptive delay)
    """
    if seconds is None:
        # Use tier-adaptive delay
        from concurrency.concurrency_config import get_concurrency_config

        config = get_concurrency_config(verbose=False)
        seconds = config.rate_limit_delay

    time.sleep(seconds)


def _run_validators(
    output: str,
    validators: List[Callable[[str], ValidationResult]],
    name: str,
    attempt: int,
    max_retries: int,
    verbose: bool,
) -> bool:
    """Run each validator over output. Returns True when all passed.

    On failure at the last attempt, raises ValueError (validator message kept);
    on earlier attempts, returns False so the caller retries the LLM call.
    """
    for i, validator in enumerate(validators):
        logger.debug(f"Agent '{name}': Running validator {i + 1}/{len(validators)}")
        result = validator(output)

        if not result.is_valid:
            logger.warning(
                f"Agent '{name}': Validation failed on attempt {attempt + 1}/{max_retries}: "
                f"{result.error_message}"
            )
            if verbose:
                safe_print(f"⚠️ Validation failed: {result.error_message}")

            if attempt < max_retries - 1:
                backoff_seconds = 2**attempt  # Exponential: 1s, 2s, 4s
                logger.debug(f"Agent '{name}': Backing off for {backoff_seconds}s")
                time.sleep(backoff_seconds)
                return False  # retry the LLM call
            error_msg = (
                f"Agent '{name}' validation failed after {max_retries} attempts: "
                f"{result.error_message}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        logger.debug(f"Agent '{name}': Validator {i + 1} passed")
    return True


def run_agent(
    model: Any,
    name: str,
    prompt_path: str,
    user_input: str,
    save_to: Optional[Path] = None,
    verbose: bool = True,
    validators: Optional[List[Callable[[str], ValidationResult]]] = None,
    max_retries: int = 3,
    skip_validation: bool = False,
    token_tracker: Optional[Any] = None,
    token_stage: Optional[str] = None,
) -> str:
    """
    Run an AI agent with given prompt and input, with optional validation.

    This function is the core execution layer for all agents in the draft pipeline.
    It handles LLM interaction, output validation, retries, and file I/O.

    Args:
        model: Configured Gemini model instance
        name: Human-readable name for the agent (for logging)
        prompt_path: Path to agent prompt file
        user_input: User's request/input for the agent
        save_to: Optional path to save output
        verbose: Whether to print progress messages
        validators: Optional list of validation functions to apply to output
        max_retries: Maximum retry attempts if validation fails (default: 3)
        skip_validation: If True, skip all validation checks (for automated runs)
        token_tracker: Optional TokenTracker fed with usage metadata
        token_stage: Label for token_tracker entries (defaults to agent name)

    Returns:
        str: Validated agent output text

    Raises:
        Exception: If agent execution fails or validation fails after all retries
    """
    if skip_validation:
        validators = None
        logger.info(f"Agent '{name}': Validation skipped (skip_validation=True)")
    if verbose:
        safe_print(f"\n{'=' * 70}")
        safe_print(f"🤖 {name}")
        safe_print(f"{'=' * 70}")

    agent_prompt = load_prompt(prompt_path)
    full_prompt = f"{agent_prompt}\n\n---\n\nUser Request:\n{user_input}"

    logger.debug(f"Agent '{name}': Starting execution")
    logger.debug(f"Prompt length: {len(full_prompt)} chars")
    logger.debug(f"Validators: {len(validators) if validators else 0}")

    output: str = ""
    start_time = time.time()

    # Empty loop detection (V3 feature): exit early if model produces empty output repeatedly
    consecutive_empty_outputs = 0
    MAX_CONSECUTIVE_EMPTY = 3

    # Retry loop with exponential backoff
    for attempt in range(max_retries):
        if verbose and attempt > 0:
            safe_print(f"Retry attempt {attempt}/{max_retries}...", end=" ", flush=True)
        elif verbose:
            safe_print("Generating...", end=" ", flush=True)

        start_time = time.time()

        try:
            # Generate LLM response. Gemini tools (Google Search/URL context) that
            # hit rate limits fall through to one direct retry without tools.
            try:
                response = model.generate_content(full_prompt)
            except Exception as tool_error:
                error_str = str(tool_error)
                if (
                    "429" in error_str
                    or "rate limit" in error_str.lower()
                    or "quota" in error_str.lower()
                ):
                    logger.warning(
                        f"Agent '{name}': Gemini tools rate limited - will retry without tools"
                    )
                    response = model.generate_content(full_prompt)
                else:
                    raise  # Re-raise if not a rate limit error

            output = _extract_response_text(response, name)

            # Defense-in-depth: scrub planning preambles, metadata, and cite_MISSING markers
            from utils.text_utils import clean_agent_output

            output = clean_agent_output(output)

            # Empty loop detection (V3 feature): track consecutive empty/trivial outputs
            if len(output.strip()) < 50:  # Effectively empty or trivial
                consecutive_empty_outputs += 1
                logger.warning(
                    f"Agent '{name}': Empty/trivial output detected "
                    f"({consecutive_empty_outputs}/{MAX_CONSECUTIVE_EMPTY})"
                )
                if consecutive_empty_outputs >= MAX_CONSECUTIVE_EMPTY:
                    logger.error(
                        f"Agent '{name}': Exiting early after "
                        f"{MAX_CONSECUTIVE_EMPTY} consecutive empty outputs"
                    )
                    if verbose:
                        safe_print(
                            f"⚠️ Model stuck - {MAX_CONSECUTIVE_EMPTY} consecutive empty outputs"
                        )
                    # Return whatever partial output we have (could be empty)
                    break
                # Continue to retry
                if attempt < max_retries - 1:
                    time.sleep(2**attempt)
                    continue
            else:
                # Reset counter on successful non-empty output
                consecutive_empty_outputs = 0

            logger.debug(
                f"Agent '{name}': Generated {len(output)} chars in {time.time() - start_time:.1f}s"
            )

            # Track token usage if tracker is provided
            if token_tracker and hasattr(response, "usage_metadata"):
                try:
                    meta = response.usage_metadata
                    token_tracker.add_call(
                        stage=token_stage or name,
                        input_tokens=getattr(meta, "prompt_token_count", 0) or 0,
                        output_tokens=getattr(meta, "candidates_token_count", 0) or 0,
                    )
                except Exception:
                    pass  # Never break generation for tracking failures

            # Validate output if validators provided (and not skipped)
            if validators and not skip_validation:
                if _run_validators(output, validators, name, attempt, max_retries, verbose):
                    logger.info(f"Agent '{name}': All {len(validators)} validators passed")
                    break
            else:
                # No validators - success on first attempt
                logger.debug(f"Agent '{name}': No validators, accepting output")
                break

        except Exception as e:
            if verbose:
                safe_print("❌ Error")

            logger.error(f"Agent '{name}': Exception on attempt {attempt + 1}: {str(e)}")

            # Track failed call if tracker is provided
            if token_tracker:
                try:
                    token_tracker.add_call(
                        stage=token_stage or name,
                        input_tokens=0,
                        output_tokens=0,
                        status=CallStatus.FAILURE,
                        error_message=str(e),
                    )
                except Exception:
                    pass  # Never break generation for tracking failures

            # If not last attempt and it's a transient error, retry
            if attempt < max_retries - 1 and _is_transient_error(e):
                backoff_seconds = 2**attempt
                logger.debug(f"Agent '{name}': Transient error, retrying after {backoff_seconds}s")
                time.sleep(backoff_seconds)
                continue

            # Partial output capture (V3 feature): on timeout, check for any files written
            if save_to and ("timeout" in str(e).lower() or "timed out" in str(e).lower()):
                partial_output = _capture_partial_output(save_to, name)
                if partial_output:
                    logger.warning(
                        f"Agent '{name}': Timeout, but captured partial output ({len(partial_output)} chars)"
                    )
                    if verbose:
                        safe_print(
                            f"⚠️ Timeout - captured {len(partial_output)} chars of partial output"
                        )
                    output = partial_output
                    break  # Exit retry loop with partial output
            raise Exception(f"Agent '{name}' execution failed: {str(e)}") from e

    elapsed = time.time() - start_time

    # Save output if path provided
    if save_to:
        try:
            save_to.parent.mkdir(parents=True, exist_ok=True)
            with open(save_to, "w", encoding="utf-8") as f:
                f.write(output)

            # Verify file was created successfully
            if not save_to.exists():
                raise IOError(f"Output file not created: {save_to}")

            file_size = save_to.stat().st_size
            if file_size == 0:
                raise IOError(f"Output file is empty: {save_to}")

            logger.info(f"Agent '{name}': Saved output to {save_to} ({file_size} bytes)")

        except Exception as e:
            logger.error(f"Agent '{name}': Failed to save output to {save_to}: {str(e)}")
            raise

    if verbose:
        safe_print(f"✅ Done ({elapsed:.1f}s, {len(output):,} chars)")
        if save_to:
            safe_print(f"Saved to: {save_to}")

    return output
