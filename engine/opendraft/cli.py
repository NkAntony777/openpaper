#!/usr/bin/env python3
"""
OpenDraft CLI - AI-Powered Research Paper Generator

A simple command-line tool for generating academic papers.

Layout: this module is only the entry point and dispatch table. Implementation
lives in focused modules —
  opendraft.ui         colors, logo, friendly errors, setup wizard, config
  opendraft.commands   human-facing content commands (tldr/digest/revise/data)
  opendraft.tool_cli   machine-facing agent-tool CLI (one JSON envelope line)
  opendraft.harness_cli  machine-facing agent-harness driver CLI
The run_* handlers are re-exported here so `from opendraft.cli import X` keeps
working for scripts and tests.
"""

import sys

# Check Python version early (before any imports)
if sys.version_info < (3, 10):
    # Nice boxed error message
    PURPLE = "\033[95m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    GRAY = "\033[90m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    print()
    print(f"  {PURPLE}╭─────────────────────────────────────────────────────────────╮{RESET}")
    print(
        f"  {PURPLE}│{RESET}                                                             {PURPLE}│{RESET}"
    )
    print(
        f"  {PURPLE}│{RESET}   {YELLOW}⚠️  OpenDraft requires Python 3.10 or higher{RESET}              {PURPLE}│{RESET}"
    )
    print(
        f"  {PURPLE}│{RESET}                                                             {PURPLE}│{RESET}"
    )
    print(
        f"  {PURPLE}│{RESET}   {GRAY}You have:{RESET} Python {sys.version_info.major}.{sys.version_info.minor}                                      {PURPLE}│{RESET}"
    )
    print(
        f"  {PURPLE}│{RESET}                                                             {PURPLE}│{RESET}"
    )
    print(
        f"  {PURPLE}│{RESET}   {BOLD}To fix, run:{RESET}                                              {PURPLE}│{RESET}"
    )
    print(
        f"  {PURPLE}│{RESET}                                                             {PURPLE}│{RESET}"
    )
    print(
        f"  {PURPLE}│{RESET}   {CYAN}conda create -n opendraft python=3.11 -y{RESET}                 {PURPLE}│{RESET}"
    )
    print(
        f"  {PURPLE}│{RESET}   {CYAN}conda activate opendraft{RESET}                                 {PURPLE}│{RESET}"
    )
    print(
        f"  {PURPLE}│{RESET}   {CYAN}pip install openpaper{RESET}                                    {PURPLE}│{RESET}"
    )
    print(
        f"  {PURPLE}│{RESET}                                                             {PURPLE}│{RESET}"
    )
    print(f"  {PURPLE}╰─────────────────────────────────────────────────────────────╯{RESET}")
    print()
    sys.exit(1)

# Suppress deprecation warnings from dependencies (Gemini SDK, weasyprint)
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Suppress WeasyPrint's stderr warnings about missing libraries
import os

os.environ["WEASYPRINT_QUIET"] = "1"

# Lazy import for version (fast, local file)
from opendraft.commands import (  # noqa: F401 (re-exported for compatibility)
    run_data_command,
    run_digest_command,
    run_revise_command,
    run_tldr_command,
)
from opendraft.harness_cli import run_harness_command  # noqa: F401 (re-export)
from opendraft.tool_cli import run_tool_command  # noqa: F401 (re-export)

# --- implementation modules -------------------------------------------------
from opendraft.ui import (  # noqa: F401 (re-exported for compatibility)
    Colors,
    get_api_key,
    get_friendly_error,
    has_api_key,
    print_friendly_error,
    run_setup,
)
from opendraft.version import __version__  # noqa: F401 (re-export)


def main():
    """Main CLI entry point."""
    import argparse

    # Handle subcommands before argparse (they have their own parsers)
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "setup":
            if run_setup():
                print()
                print(f"  {Colors.BOLD}Verifying installation...{Colors.RESET}")
                print()
                from opendraft.verify import verify_installation

                return verify_installation()
            return 1
        if cmd == "verify":
            from opendraft.verify import verify_installation

            return verify_installation()
        if cmd == "tldr":
            return run_tldr_command(sys.argv[2:])
        if cmd == "digest":
            return run_digest_command(sys.argv[2:])
        if cmd == "revise":
            return run_revise_command(sys.argv[2:])
        if cmd == "data":
            return run_data_command(sys.argv[2:])
        if cmd == "tool":
            return run_tool_command(sys.argv[2:])
        if cmd == "harness":
            return run_harness_command(sys.argv[2:])

    parser = argparse.ArgumentParser(
        prog="opendraft",
        description="Agent tool layer + pi writing harness for academic drafts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{Colors.BOLD}Usage:{Colors.RESET}
  opendraft setup              Configure API key + verify installation
  opendraft verify             Check system dependencies (PDF, LaTeX)
  opendraft tool <name>        Run an agent tool (JSON envelope on stdout)
  opendraft harness section    Drive one paper section via the pi agent loop
  opendraft harness review     Global cross-section review via the pi agent loop
  opendraft harness paper      Orchestrate sections → review → fixes → finish gate
  opendraft harness distill    Distill run_journal.jsonl into proposed lessons
  opendraft harness eval       Offline eval-suite metrics for a paper directory
  opendraft tldr <file>        Generate 5-bullet TL;DR for any paper
  opendraft digest <file>      Generate 60-second audio digest
  opendraft revise <folder> "instructions"   Revise existing draft
  opendraft data <provider> <query>          Fetch research datasets

{Colors.BOLD}Examples:{Colors.RESET}
  opendraft tool list
  opendraft harness section --root ./paper --section literature_review
  opendraft harness review --root ./paper
  opendraft harness paper --root ./paper
  opendraft harness distill --root ./paper
  opendraft harness eval --root ./paper
  opendraft tldr paper.pdf
  opendraft digest paper.pdf --voice josh
  opendraft revise ./output "make the intro longer"
  opendraft data worldbank NY.GDP.MKTP.CD --countries USA;DEU;FRA

{Colors.GRAY}https://github.com/NkAntony777/openpaper{Colors.RESET}
        """,
    )

    parser.add_argument("--version", "-v", action="version", version=f"opendraft {__version__}")

    parser.parse_args()  # validate flags (--version exits here)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
