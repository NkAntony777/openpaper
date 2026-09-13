#!/usr/bin/env python3
"""
ABOUTME: Human-facing CLI chrome: ANSI colors, the ASCII logo, friendly error
ABOUTME: rendering, the API-key setup wizard, and ~/.opendraft config storage.
ABOUTME: Nothing here is on the machine-contract path (`opendraft tool` /
ABOUTME: `opendraft harness` never import this module's printing helpers).
"""

import json
import os
import subprocess
from pathlib import Path

from opendraft.version import __version__

# Config directory for storing API keys
CONFIG_DIR = Path.home() / ".opendraft"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Where users report bugs (OpenPaper fork)
ISSUES_URL = "https://github.com/NkAntony777/openpaper/issues"


# ANSI color codes
class Colors:
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    GRAY = "\033[90m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"
    UNDERLINE = "\033[4m"


def get_friendly_error(e: Exception) -> tuple:
    """
    Convert technical exceptions to user-friendly messages.

    Returns:
        Tuple of (friendly_message, hint) or (None, None) if no friendly version
    """
    error_str = str(e).lower()

    c = Colors

    # API Key errors
    if "api key not valid" in error_str or "invalid api key" in error_str:
        return (
            "Your API key is invalid or expired.",
            f"Run {c.CYAN}opendraft setup{c.RESET} to enter a new key.",
        )

    if "api_key_invalid" in error_str or "permission_denied" in error_str:
        return (
            "API key doesn't have permission for this operation.",
            f"Check your key at {c.CYAN}https://aistudio.google.com/apikey{c.RESET}",
        )

    # Rate limiting
    if (
        "429" in error_str
        or "rate limit" in error_str
        or "resource exhausted" in error_str
        or "quota" in error_str
    ):
        return (
            "Rate limited by the API.",
            "Wait a minute and try again. Free tier has usage limits.",
        )

    # Network errors
    if "connection" in error_str and ("error" in error_str or "failed" in error_str):
        return ("Network connection failed.", "Check your internet connection and try again.")

    if "timeout" in error_str:
        return ("Request timed out.", "The server took too long to respond. Try again.")

    if "ssl" in error_str or "certificate" in error_str:
        return ("Secure connection failed.", "Check your network/VPN settings and try again.")

    # DNS/hostname errors
    if "name or service not known" in error_str or "getaddrinfo failed" in error_str:
        return ("Can't reach the server.", "Check your internet connection.")

    # Content/safety filters
    if "safety" in error_str or "blocked" in error_str or "harmful" in error_str:
        return (
            "Content was blocked by safety filters.",
            "Try rephrasing your topic or using different keywords.",
        )

    # Model errors
    if "model not found" in error_str or "model" in error_str and "not available" in error_str:
        return ("AI model is temporarily unavailable.", "Try again in a few minutes.")

    # Insufficient citations (common during research)
    if "insufficient citations" in error_str:
        return (
            "Couldn't find enough sources for this topic.",
            "Try a more specific or different research topic.",
        )

    # PDF/export errors
    if "pdf" in error_str and ("failed" in error_str or "error" in error_str):
        return ("PDF generation failed.", "The Word document (.docx) should still be available.")

    if "weasyprint" in error_str or "cairo" in error_str or "pango" in error_str:
        return (
            "PDF library not properly installed.",
            f"Run {c.CYAN}opendraft verify{c.RESET} to check dependencies.",
        )

    # File/permission errors
    if "permission denied" in error_str or "errno 13" in error_str:
        return (
            "Permission denied when writing files.",
            "Try a different output directory or check folder permissions.",
        )

    if "no space" in error_str or "disk full" in error_str:
        return ("Disk is full.", "Free up some space and try again.")

    # Memory errors
    if "memory" in error_str or isinstance(e, MemoryError):
        return ("Ran out of memory.", "Close other apps and try again, or try a shorter topic.")

    # Recursion (rare but possible)
    if "recursion" in error_str or "maximum recursion" in error_str:
        return ("Something went wrong (recursion limit).", f"Please report this at {ISSUES_URL}")

    # JSON parsing errors (malformed API response)
    if "json" in error_str and ("decode" in error_str or "parse" in error_str):
        return ("Received invalid response from server.", "Try again in a few minutes.")

    # Encoding errors
    if "encode" in error_str or "decode" in error_str or "codec" in error_str:
        return ("Text encoding error.", "Try using a simpler topic without special characters.")

    # File not found (missing dependency files)
    if "no such file" in error_str or "file not found" in error_str:
        return (
            "A required file is missing.",
            f"Try reinstalling: {c.CYAN}pip install --force-reinstall openpaper{c.RESET}",
        )

    # No friendly version found
    return (None, None)


def print_friendly_error(e: Exception):
    """Print a user-friendly error message for common exceptions."""
    c = Colors

    friendly_msg, hint = get_friendly_error(e)

    if friendly_msg:
        print()
        print(f"  {c.RED}✗{c.RESET} {friendly_msg}")
        if hint:
            print(f"    {c.GRAY}{hint}{c.RESET}")
        print()
    else:
        # Fallback: show original error but clean it up a bit
        error_str = str(e)
        # Remove common technical prefixes
        for prefix in [
            "google.api_core.exceptions.",
            "requests.exceptions.",
            "urllib3.exceptions.",
            "httpx.",
        ]:
            error_str = error_str.replace(prefix, "")

        print()
        print(f"  {c.RED}✗{c.RESET} {error_str}")
        print()
        print(f"  {c.GRAY}If this keeps happening, report at:{c.RESET}")
        print(f"  {c.CYAN}{ISSUES_URL}{c.RESET}")
        print()


def get_saved_config():
    """Load saved configuration."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(config):
    """Save configuration to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def has_api_key():
    """Check if API key is configured."""
    if os.getenv("GOOGLE_API_KEY"):
        return True
    config = get_saved_config()
    return bool(config.get("google_api_key"))


def get_api_key():
    """Get API key from environment or config."""
    key = os.getenv("GOOGLE_API_KEY")
    if key:
        return key
    config = get_saved_config()
    return config.get("google_api_key", "")


def clear_screen():
    """Clear terminal screen."""
    try:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "cls"], check=False)
        else:
            subprocess.run(["clear"], check=False)
    except (FileNotFoundError, OSError):
        # Fallback: print newlines if clear command not available
        print("\n" * 50)


def print_logo():
    """Print ASCII art logo."""
    c = Colors
    logo = f"""
{c.PURPLE}{c.BOLD}  ┌─────────────────────────────────────────────────────┐
  │                                                     │
  │   ██████╗ ██████╗ ███████╗███╗   ██╗               │
  │  ██╔═══██╗██╔══██╗██╔════╝████╗  ██║               │
  │  ██║   ██║██████╔╝█████╗  ██╔██╗ ██║               │
  │  ██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║               │
  │  ╚██████╔╝██║     ███████╗██║ ╚████║               │
  │   ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝               │
  │  ██████╗ ██████╗  █████╗ ███████╗████████╗         │
  │  ██╔══██╗██╔══██╗██╔══██╗██╔════╝╚══██╔══╝         │
  │  ██║  ██║██████╔╝███████║█████╗     ██║            │
  │  ██║  ██║██╔══██╗██╔══██║██╔══╝     ██║            │
  │  ██████╔╝██║  ██║██║  ██║██║        ██║            │
  │  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝        ╚═╝            │
  │                                                     │
  └─────────────────────────────────────────────────────┘{c.RESET}
"""
    print(logo)


def print_header():
    """Print clean header with logo."""
    c = Colors
    print_logo()
    print(f"  {c.GRAY}AI Research Paper Generator{c.RESET}  {c.DIM}v{__version__}{c.RESET}")
    print()


def print_divider():
    """Print a subtle divider."""
    print(f"  {Colors.GRAY}{'─' * 50}{Colors.RESET}")


def run_setup():
    """Interactive setup wizard."""
    c = Colors
    clear_screen()
    print_header()

    print(f"  {c.BOLD}Setup{c.RESET}")
    print_divider()
    print()
    print(f"  You need a {c.BOLD}Google AI API key{c.RESET} (free).")
    print()

    # Auto-open browser
    api_url = "https://aistudio.google.com/apikey"
    try:
        import webbrowser

        webbrowser.open(api_url)
        print(f"  {c.GREEN}✓{c.RESET} Opened {c.UNDERLINE}{api_url}{c.RESET} in browser")
    except Exception:
        print(f"  {c.CYAN}1.{c.RESET} Open {c.UNDERLINE}{api_url}{c.RESET}")

    print()
    print(f"  {c.CYAN}→{c.RESET} Click {c.BOLD}Create API Key{c.RESET}, then copy and paste below")
    print()

    try:
        api_key = input(f"  {c.PURPLE}›{c.RESET} API Key: ").strip()
    except (KeyboardInterrupt, EOFError):
        print(f"\n\n  {c.GRAY}Cancelled.{c.RESET}\n")
        return False

    if not api_key:
        print(f"\n  {c.RED}✗{c.RESET} No key provided.\n")
        return False

    if len(api_key) < 20:
        print(f"\n  {c.RED}✗{c.RESET} Invalid key format.\n")
        return False

    config = get_saved_config()
    config["google_api_key"] = api_key
    save_config(config)
    os.environ["GOOGLE_API_KEY"] = api_key

    print()
    print(f"  {c.GREEN}✓{c.RESET} API key saved to {c.GRAY}~/.opendraft/config.json{c.RESET}")
    print()
    return True
