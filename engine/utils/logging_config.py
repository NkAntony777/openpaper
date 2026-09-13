#!/usr/bin/env python3
"""
ABOUTME: Centralized logging configuration for OpenDraft system
ABOUTME: Provides consistent logging format, levels, and handlers across all modules

Usage:
    from utils.logging_config import get_logger

    logger = get_logger(__name__)
    logger.info("Starting citation research...")
    logger.warning("API rate limit approaching")
    logger.error("Failed to scrape title", exc_info=True)

Features:
- Console and file logging
- Color-coded output for terminal
- Structured format with timestamps
- Automatic log rotation (10MB per file, 5 backups)
- Module-specific loggers with hierarchy
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

# Log directory (created if doesn't exist)
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# Log file paths
MAIN_LOG_FILE = LOG_DIR / "opendraft.log"
ERROR_LOG_FILE = LOG_DIR / "errors.log"

# Log format
LOG_FORMAT = "%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s"
LOG_FORMAT_SIMPLE = "%(message)s"  # CLI-friendly format (no timestamps/modules)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Track CLI mode state
_cli_mode_enabled = False

# ANSI color codes for terminal output
COLORS = {
    "DEBUG": "\033[36m",  # Cyan
    "INFO": "\033[32m",  # Green
    "WARNING": "\033[33m",  # Yellow
    "ERROR": "\033[31m",  # Red
    "CRITICAL": "\033[35m",  # Magenta
    "RESET": "\033[0m",  # Reset
}


class ColoredFormatter(logging.Formatter):
    """
    Custom formatter that adds color to console output.

    Colors:
    - DEBUG: Cyan
    - INFO: Green
    - WARNING: Yellow
    - ERROR: Red
    - CRITICAL: Magenta
    """

    def format(self, record):
        """Add color codes to log level in terminal output."""
        levelname = record.levelname
        if levelname in COLORS:
            record.levelname = f"{COLORS[levelname]}{levelname}{COLORS['RESET']}"
        return super().format(record)


class CLIFormatter(logging.Formatter):
    """
    CLI-friendly formatter that transforms technical logs into readable messages.

    Features:
    - Removes timestamps and module names
    - Converts technical messages to user-friendly format
    - Adds emoji indicators for different message types
    - Suppresses overly verbose messages
    """

    # Messages to suppress entirely (too technical for CLI users)
    # These are exact substring matches
    SUPPRESS_SUBSTRINGS = [
        "[MEMORY]",  # Memory tracking
        "[SETUP]",  # Setup messages
        "Validation skipped",  # Internal validation state
        "Validation:",  # Validation status
        "File size:",  # File size logs
        "Target:",  # Target word count logs
        "Output:",  # Output path logs (we show final paths)
        "Output directory:",  # Output directory logs
        "Process PID:",  # Process ID
        "Python:",  # Python version
        "Tracker:",  # Tracker status
        "Streamer:",  # Streamer status
        "Topic:",  # Topic echo (already shown in banner)
        "Language:",  # Language echo
        "Academic Level:",  # Level echo
        "Author:",  # Author echo
        "Institution:",  # Institution echo
        "AUTOMATED WORKFLOW",  # Technical header
        "Created folders:",  # Folder creation details
        "Saved to:",  # File save paths
        "Paragraph-truncated",  # Internal truncation logs
        "DRAFT GENERATION",  # Draft generation headers
        "Model initialized",  # Model init messages
        "Crossref:",  # Individual citation finds
        "SemanticScholar:",  # Individual citation finds
        "Gemini grounded",  # Gemini grounding messages
        "retry attempts failed",  # API retries (normal with fallbacks)
        "API unavailable after",  # API unavailable (normal with fallbacks)
    ]

    # Regex patterns to suppress
    SUPPRESS_REGEX = [
        r"^=+$",  # Divider lines of just =
        r"^-+$",  # Divider lines of just -
        r"Loaded \d+ cached",  # Cache loading messages
        r"Loaded \d+ prox",  # Proxy loading
    ]

    # Transform patterns: (pattern, replacement)
    TRANSFORM_PATTERNS = [
        # Agent completion messages - clean up file paths
        (r"Agent '([^']+)'.*?Saved output.*", r"✅ \1 complete"),
        # Chapter writing
        (r"\[CHAPTER (\d+)/(\d+)\] Starting (.+)", r"📝 Writing chapter \1/\2: \3"),
        (r"\[CHAPTER (\d+)/(\d+)\] ✅ Complete.*", r"✅ Chapter \1/\2 done"),
        # QA checks
        (r"\[QA (\d+)/(\d+)\] Running (.+) agent.*", r"🔍 Quality check \1/\2: \3"),
        (r"\[QA (\d+)/(\d+)\] ✅ .+ complete.*", r"✅ Quality check \1/\2 done"),
        # Phase headers (from logger, not print - these have more detail)
        (r"PHASE (\d+\.?\d*):?\s*(.+)", r"\n📋 PHASE \1: \2"),
        (r"PHASE (\d+\.?\d*) COMPLETE.*", r"✅ Phase \1 complete"),
        # Research stages - strip trailing words
        (r"Research plan created.*", r"✅ Research plan ready"),
        (r"Citations.*verified.*", r"✅ Citations verified"),
        (r"Draft compiled.*", r"✅ Draft compiled"),
        (r"Exporting PDF.*", r"📄 Creating PDF..."),
        (r"PDF created successfully.*", r"✅ PDF created"),
        (r"DOCX created successfully.*", r"✅ Word document created"),
        # Abstract generation
        (r"Abstract generated.*", r"✅ Abstract generated"),
        (r"Abstract integrated.*", r"✅ Abstract added to draft"),
    ]

    def format(self, record):
        """Transform technical logs into user-friendly messages."""
        import re

        msg = record.getMessage()

        # First check exact substring suppressions
        for substring in self.SUPPRESS_SUBSTRINGS:
            if substring in msg:
                return ""  # Suppress

        # Check regex suppressions
        for pattern in self.SUPPRESS_REGEX:
            if re.search(pattern, msg):
                return ""  # Suppress

        # Apply transformations for known patterns
        for pattern, replacement in self.TRANSFORM_PATTERNS:
            match = re.search(pattern, msg, re.IGNORECASE)
            if match:
                transformed = re.sub(pattern, replacement, msg, flags=re.IGNORECASE)
                # Clean up and return with indent
                return f"  {transformed.strip()}"

        # For ERROR level, add red indicator
        if record.levelno >= logging.ERROR:
            return f"  ❌ {msg}"

        # For WARNING level, add yellow indicator (but skip rate limit spam)
        if record.levelno >= logging.WARNING:
            if "rate limit" in msg.lower():
                return ""  # Suppress rate limit warnings
            return f"  ⚠️  {msg}"

        # Default: show message with indent (for user-friendly messages from print())
        # But suppress very technical messages
        if any(x in msg for x in ["logger", "DEBUG", "bytes"]):
            return ""

        return f"  {msg}"


def setup_logging(
    level: int = logging.INFO, console_output: bool = True, file_output: bool = True
) -> None:
    """
    Configure root logger with console and file handlers.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        console_output: Enable console logging
        file_output: Enable file logging

    Example:
        # Development mode (verbose console, no files)
        setup_logging(level=logging.DEBUG, file_output=False)

        # Production mode (quiet console, detailed files)
        setup_logging(level=logging.WARNING, console_output=True, file_output=True)
    """
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture all levels, filter in handlers

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Console handler (color-coded, INFO+)
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = ColoredFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

    # File handler (all logs, rotating)
    if file_output:
        # Main log file (all levels)
        file_handler = logging.handlers.RotatingFileHandler(
            MAIN_LOG_FILE,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

        # Error log file (ERROR+ only)
        error_handler = logging.handlers.RotatingFileHandler(
            ERROR_LOG_FILE,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_formatter)
        root_logger.addHandler(error_handler)


def get_logger(name: str, level: Optional[int] = None) -> logging.Logger:
    """
    Get a logger instance for a module.

    Args:
        name: Module name (typically __name__)
        level: Optional logging level override

    Returns:
        Logger instance configured with module name

    Example:
        # In utils/deduplicate_citations.py
        logger = get_logger(__name__)
        logger.info("Starting deduplication...")

        # In tests/test_citations.py (debug mode)
        logger = get_logger(__name__, level=logging.DEBUG)
    """
    logger = logging.getLogger(name)

    # Set level if specified
    if level is not None:
        logger.setLevel(level)

    return logger


# Initialize logging on module import (can be reconfigured later)
setup_logging(level=logging.INFO, console_output=True, file_output=True)


class CLIFilter(logging.Filter):
    """Filter that removes log records with empty formatted messages."""

    def filter(self, record):
        """Return False to suppress records that format to empty strings."""
        # Get the formatter from the handler
        # We check the message content - if it would be suppressed, filter it out
        msg = record.getMessage()

        # Check if this message would be suppressed by CLIFormatter
        for substring in CLIFormatter.SUPPRESS_SUBSTRINGS:
            if substring in msg:
                return False

        import re

        for pattern in CLIFormatter.SUPPRESS_REGEX:
            if re.search(pattern, msg):
                return False

        return True


def enable_cli_mode() -> None:
    """
    Enable CLI-friendly logging mode for end users.

    Transforms technical log messages into clean, readable output:
    - Removes timestamps and module names
    - Converts technical messages to user-friendly format
    - Adds emoji indicators
    - Suppresses overly verbose messages

    Call this before starting draft generation in CLI mode.

    Example:
        from utils.logging_config import enable_cli_mode
        enable_cli_mode()
        # Now all logs will be user-friendly
    """
    global _cli_mode_enabled

    if _cli_mode_enabled:
        return  # Already enabled

    root_logger = logging.getLogger()

    # Find and replace console handler formatter
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
            cli_formatter = CLIFormatter()
            handler.setFormatter(cli_formatter)
            # Add filter to prevent empty lines
            handler.addFilter(CLIFilter())
            break

    _cli_mode_enabled = True


def disable_cli_mode() -> None:
    """
    Disable CLI-friendly mode and restore standard logging format.

    Restores the detailed timestamp + module name format.
    """
    global _cli_mode_enabled

    if not _cli_mode_enabled:
        return

    root_logger = logging.getLogger()

    # Find and restore console handler formatter
    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler) and handler.stream == sys.stdout:
            standard_formatter = ColoredFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)
            handler.setFormatter(standard_formatter)
            break

    _cli_mode_enabled = False


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

if __name__ == "__main__":
    """Test logging configuration with sample messages."""

    print("Testing logging configuration...\n")

    # Get logger for this module
    logger = get_logger(__name__)

    # Test all log levels
    logger.debug("Debug message - detailed execution info")
    logger.info("Info message - normal operation")
    logger.warning("Warning message - potential issue")
    logger.error("Error message - operation failed")
    logger.critical("Critical message - system failure")

    # Test structured logging
    logger.info(
        "Citation scraped successfully",
        extra={"citation_id": "cite_042", "source": "Crossref", "title": "Example Paper"},
    )

    # Test exception logging
    try:
        raise ValueError("Simulated error for testing")
    except Exception:
        logger.error("Exception occurred during test", exc_info=True)

    print("\n✅ Logs written to:")
    print(f"   Main log: {MAIN_LOG_FILE}")
    print(f"   Error log: {ERROR_LOG_FILE}")
