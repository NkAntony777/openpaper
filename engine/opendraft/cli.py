#!/usr/bin/env python3
"""
OpenDraft CLI - AI-Powered Research Paper Generator

A simple, interactive command-line tool for generating academic papers.
"""

import sys

# Check Python version early (before any imports)
if sys.version_info < (3, 10):
    # Nice boxed error message
    PURPLE = '\033[95m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    GRAY = '\033[90m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

    print()
    print(f"  {PURPLE}╭─────────────────────────────────────────────────────────────╮{RESET}")
    print(f"  {PURPLE}│{RESET}                                                             {PURPLE}│{RESET}")
    print(f"  {PURPLE}│{RESET}   {YELLOW}⚠️  OpenDraft requires Python 3.10 or higher{RESET}              {PURPLE}│{RESET}")
    print(f"  {PURPLE}│{RESET}                                                             {PURPLE}│{RESET}")
    print(f"  {PURPLE}│{RESET}   {GRAY}You have:{RESET} Python {sys.version_info.major}.{sys.version_info.minor}                                      {PURPLE}│{RESET}")
    print(f"  {PURPLE}│{RESET}                                                             {PURPLE}│{RESET}")
    print(f"  {PURPLE}│{RESET}   {BOLD}To fix, run:{RESET}                                              {PURPLE}│{RESET}")
    print(f"  {PURPLE}│{RESET}                                                             {PURPLE}│{RESET}")
    print(f"  {PURPLE}│{RESET}   {CYAN}conda create -n opendraft python=3.11 -y{RESET}                  {PURPLE}│{RESET}")
    print(f"  {PURPLE}│{RESET}   {CYAN}conda activate opendraft{RESET}                                  {PURPLE}│{RESET}")
    print(f"  {PURPLE}│{RESET}   {CYAN}pip install opendraft{RESET}                                     {PURPLE}│{RESET}")
    print(f"  {PURPLE}│{RESET}                                                             {PURPLE}│{RESET}")
    print(f"  {PURPLE}╰─────────────────────────────────────────────────────────────╯{RESET}")
    print()
    sys.exit(1)

# Suppress deprecation warnings from dependencies (Gemini SDK, weasyprint)
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Suppress WeasyPrint's stderr warnings about missing libraries
import os
os.environ['WEASYPRINT_QUIET'] = '1'

# Minimal imports for fast startup
import json
from pathlib import Path

# Lazy import for version (fast, local file)
from opendraft.version import __version__

# Config directory for storing API keys
CONFIG_DIR = Path.home() / '.opendraft'
CONFIG_FILE = CONFIG_DIR / 'config.json'

# ANSI color codes
class Colors:
    PURPLE = '\033[95m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    GRAY = '\033[90m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'
    UNDERLINE = '\033[4m'


def get_friendly_error(e: Exception) -> tuple:
    """
    Convert technical exceptions to user-friendly messages.

    Returns:
        Tuple of (friendly_message, hint) or (None, None) if no friendly version
    """
    error_str = str(e).lower()
    error_type = type(e).__name__

    c = Colors

    # API Key errors
    if 'api key not valid' in error_str or 'invalid api key' in error_str:
        return (
            "Your API key is invalid or expired.",
            f"Run {c.CYAN}opendraft setup{c.RESET} to enter a new key."
        )

    if 'api_key_invalid' in error_str or 'permission_denied' in error_str:
        return (
            "API key doesn't have permission for this operation.",
            f"Check your key at {c.CYAN}https://aistudio.google.com/apikey{c.RESET}"
        )

    # Rate limiting
    if '429' in error_str or 'rate limit' in error_str or 'resource exhausted' in error_str or 'quota' in error_str:
        return (
            "Rate limited by the API.",
            "Wait a minute and try again. Free tier has usage limits."
        )

    # Network errors
    if 'connection' in error_str and ('error' in error_str or 'failed' in error_str):
        return (
            "Network connection failed.",
            "Check your internet connection and try again."
        )

    if 'timeout' in error_str:
        return (
            "Request timed out.",
            "The server took too long to respond. Try again."
        )

    if 'ssl' in error_str or 'certificate' in error_str:
        return (
            "Secure connection failed.",
            "Check your network/VPN settings and try again."
        )

    # DNS/hostname errors
    if 'name or service not known' in error_str or 'getaddrinfo failed' in error_str:
        return (
            "Can't reach the server.",
            "Check your internet connection."
        )

    # Content/safety filters
    if 'safety' in error_str or 'blocked' in error_str or 'harmful' in error_str:
        return (
            "Content was blocked by safety filters.",
            "Try rephrasing your topic or using different keywords."
        )

    # Model errors
    if 'model not found' in error_str or 'model' in error_str and 'not available' in error_str:
        return (
            "AI model is temporarily unavailable.",
            "Try again in a few minutes."
        )

    # Insufficient citations (common during research)
    if 'insufficient citations' in error_str:
        return (
            "Couldn't find enough sources for this topic.",
            "Try a more specific or different research topic."
        )

    # PDF/export errors
    if 'pdf' in error_str and ('failed' in error_str or 'error' in error_str):
        return (
            "PDF generation failed.",
            f"The Word document (.docx) should still be available."
        )

    if 'weasyprint' in error_str or 'cairo' in error_str or 'pango' in error_str:
        return (
            "PDF library not properly installed.",
            f"Run {c.CYAN}opendraft verify{c.RESET} to check dependencies."
        )

    # File/permission errors
    if 'permission denied' in error_str or 'errno 13' in error_str:
        return (
            "Permission denied when writing files.",
            "Try a different output directory or check folder permissions."
        )

    if 'no space' in error_str or 'disk full' in error_str:
        return (
            "Disk is full.",
            "Free up some space and try again."
        )

    # Memory errors
    if 'memory' in error_str or isinstance(e, MemoryError):
        return (
            "Ran out of memory.",
            "Close other apps and try again, or try a shorter topic."
        )

    # Recursion (rare but possible)
    if 'recursion' in error_str or 'maximum recursion' in error_str:
        return (
            "Something went wrong (recursion limit).",
            "Please report this at github.com/federicodeponte/opendraft/issues"
        )

    # JSON parsing errors (malformed API response)
    if 'json' in error_str and ('decode' in error_str or 'parse' in error_str):
        return (
            "Received invalid response from server.",
            "Try again in a few minutes."
        )

    # Encoding errors
    if 'encode' in error_str or 'decode' in error_str or 'codec' in error_str:
        return (
            "Text encoding error.",
            "Try using a simpler topic without special characters."
        )

    # File not found (missing dependency files)
    if 'no such file' in error_str or 'file not found' in error_str:
        return (
            "A required file is missing.",
            f"Try reinstalling: {c.CYAN}pip install --force-reinstall opendraft{c.RESET}"
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
        for prefix in ['google.api_core.exceptions.', 'requests.exceptions.',
                       'urllib3.exceptions.', 'httpx.']:
            error_str = error_str.replace(prefix, '')

        print()
        print(f"  {c.RED}✗{c.RESET} {error_str}")
        print()
        print(f"  {c.GRAY}If this keeps happening, report at:{c.RESET}")
        print(f"  {c.CYAN}https://github.com/federicodeponte/opendraft/issues{c.RESET}")
        print()


def get_saved_config():
    """Load saved configuration."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except:
            return {}
    return {}


def save_config(config):
    """Save configuration to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def has_api_key():
    """Check if API key is configured."""
    if os.getenv('GOOGLE_API_KEY'):
        return True
    config = get_saved_config()
    return bool(config.get('google_api_key'))


def get_api_key():
    """Get API key from environment or config."""
    key = os.getenv('GOOGLE_API_KEY')
    if key:
        return key
    config = get_saved_config()
    return config.get('google_api_key', '')


def clear_screen():
    """Clear terminal screen."""
    import subprocess
    try:
        if os.name == 'nt':
            subprocess.run(['cmd', '/c', 'cls'], check=False)
        else:
            subprocess.run(['clear'], check=False)
    except (FileNotFoundError, OSError):
        # Fallback: print newlines if clear command not available
        print('\n' * 50)


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
    except:
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
    config['google_api_key'] = api_key
    save_config(config)
    os.environ['GOOGLE_API_KEY'] = api_key

    print()
    print(f"  {c.GREEN}✓{c.RESET} API key saved to {c.GRAY}~/.opendraft/config.json{c.RESET}")
    print()
    return True


def run_tldr_command(argv):
    """Run TL;DR subcommand."""
    import argparse
    c = Colors

    parser = argparse.ArgumentParser(
        prog="opendraft tldr",
        description="Generate 5-bullet TL;DR summary for any paper"
    )
    parser.add_argument("document", help="Path to document (PDF, MD, or TXT)")
    parser.add_argument("--output", "-o", help="Output file path")

    args = parser.parse_args(argv)
    document_path = Path(args.document)

    if not document_path.exists():
        print(f"\n  {c.RED}✗{c.RESET} File not found: {document_path}\n")
        return 1

    print()
    print(f"  {c.BOLD}TL;DR{c.RESET}")
    print(f"  {c.GRAY}{'─' * 40}{c.RESET}")
    print(f"  {c.GRAY}Document:{c.RESET} {document_path.name}")

    try:
        # Import here to avoid slow startup
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from tldr import generate_tldr
        from utils.document_reader import get_document_info

        info = get_document_info(document_path)
        print(f"  {c.GRAY}Words:{c.RESET}    {info['word_count']:,}")
        print()
        print(f"  {c.PURPLE}⣾{c.RESET} Generating TL;DR...")

        tldr = generate_tldr(document_path)

        print()
        print(f"  {c.GREEN}{'─' * 40}{c.RESET}")
        # Print TL;DR with nice formatting
        for line in tldr.split('\n'):
            if line.strip():
                print(f"  {line}")
        print(f"  {c.GREEN}{'─' * 40}{c.RESET}")

        if args.output:
            output_path = Path(args.output)
            output_path.write_text(tldr, encoding="utf-8")
            print(f"\n  {c.GREEN}✓{c.RESET} Saved to: {output_path}")

        print()
        return 0

    except Exception as e:
        print_friendly_error(e)
        return 1


def run_digest_command(argv):
    """Run digest subcommand."""
    import argparse
    c = Colors

    parser = argparse.ArgumentParser(
        prog="opendraft digest",
        description="Generate 60-second audio digest for any paper"
    )
    parser.add_argument("document", help="Path to document (PDF, MD, or TXT)")
    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument(
        "--voice",
        default="rachel",
        choices=["rachel", "adam", "josh", "elli", "bella"],
        help="ElevenLabs voice (default: rachel)"
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Skip audio generation (script only)"
    )

    args = parser.parse_args(argv)
    document_path = Path(args.document)

    if not document_path.exists():
        print(f"\n  {c.RED}✗{c.RESET} File not found: {document_path}\n")
        return 1

    print()
    print(f"  {c.BOLD}Digest{c.RESET}")
    print(f"  {c.GRAY}{'─' * 40}{c.RESET}")
    print(f"  {c.GRAY}Document:{c.RESET} {document_path.name}")
    print(f"  {c.GRAY}Voice:{c.RESET}    {args.voice}")

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from digest import generate_digest
        from utils.document_reader import get_document_info

        info = get_document_info(document_path)
        print(f"  {c.GRAY}Words:{c.RESET}    {info['word_count']:,}")
        print()
        print(f"  {c.PURPLE}⣾{c.RESET} Generating digest...")

        output_dir = Path(args.output) if args.output else None

        result = generate_digest(
            document_path,
            output_dir=output_dir,
            voice=args.voice,
            generate_audio=not args.no_audio,
        )

        print()
        print(f"  {c.GREEN}{'─' * 40}{c.RESET}")
        print(f"  {result['script']}")
        print(f"  {c.GREEN}{'─' * 40}{c.RESET}")
        print()
        print(f"  {c.GRAY}Words:{c.RESET} {result['word_count']}")
        print(f"  {c.GREEN}✓{c.RESET} Script: {result['script_path']}")

        if "audio_path" in result:
            print(f"  {c.GREEN}✓{c.RESET} Audio:  {result['audio_path']}")
        elif "audio_error" in result:
            print(f"  {c.YELLOW}!{c.RESET} Audio skipped: {result['audio_error']}")
            print(f"    {c.GRAY}Set ELEVENLABS_API_KEY to enable audio{c.RESET}")

        print()
        return 0

    except Exception as e:
        print_friendly_error(e)
        return 1


def run_revise_command(argv):
    """Run revise subcommand."""
    import argparse
    c = Colors

    parser = argparse.ArgumentParser(
        prog="opendraft revise",
        description="Revise an existing draft with AI assistance"
    )
    parser.add_argument("target", help="Path to draft folder or markdown file")
    parser.add_argument("instructions", help="Revision instructions (e.g., 'make the introduction longer')")
    parser.add_argument("--model", "-m", default="gemini-3-flash-preview",
                        help="Gemini model to use (default: gemini-3-flash-preview)")

    args = parser.parse_args(argv)
    target_path = Path(args.target)

    if not target_path.exists():
        print(f"\n  {c.RED}✗{c.RESET} Path not found: {target_path}\n")
        return 1

    print()
    print(f"  {c.BOLD}Revise{c.RESET}")
    print(f"  {c.GRAY}{'─' * 40}{c.RESET}")
    print(f"  {c.GRAY}Target:{c.RESET}       {target_path}")
    print(f"  {c.GRAY}Instructions:{c.RESET} {args.instructions[:50]}{'...' if len(args.instructions) > 50 else ''}")
    print()

    # Ensure API key is set
    if not has_api_key():
        print(f"  {c.YELLOW}!{c.RESET} Run {c.BOLD}opendraft setup{c.RESET} first.\n")
        return 1

    if not os.getenv('GOOGLE_API_KEY'):
        os.environ['GOOGLE_API_KEY'] = get_api_key()

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from utils.revise import revise_draft, find_draft_in_folder

        # Show which file will be revised
        if target_path.is_dir():
            draft_path = find_draft_in_folder(target_path)
            if draft_path:
                print(f"  {c.GRAY}Found draft:{c.RESET} {draft_path.name}")
            else:
                print(f"\n  {c.RED}✗{c.RESET} No draft found in {target_path}\n")
                return 1
        print()
        print(f"  {c.PURPLE}⣾{c.RESET} Revising draft...")

        result = revise_draft(target_path, args.instructions, model=args.model)

        print()
        print(f"  {c.GREEN}{'─' * 40}{c.RESET}")
        print(f"  {c.GREEN}✓{c.RESET} {c.BOLD}Revision complete!{c.RESET}")
        print(f"  {c.GREEN}{'─' * 40}{c.RESET}")
        print()

        # Score changes
        delta_color = c.GREEN if result['delta'] >= 0 else c.RED
        delta_sign = "+" if result['delta'] >= 0 else ""
        print(f"  {c.GRAY}Quality:{c.RESET} {result['score_before']} → {result['score_after']} ({delta_color}{delta_sign}{result['delta']}{c.RESET})")
        print(f"  {c.GRAY}Words:{c.RESET}   {result['word_count_before']:,} → {result['word_count']:,}")

        print()
        print(f"  {c.GRAY}Files:{c.RESET}")
        print(f"    {c.CYAN}📝{c.RESET} {result['md_path']}")
        if result['pdf_path']:
            print(f"    {c.CYAN}📄{c.RESET} {result['pdf_path']}")
        if result['docx_path']:
            print(f"    {c.CYAN}📑{c.RESET} {result['docx_path']}")
        print()

        return 0

    except Exception as e:
        print_friendly_error(e)
        return 1


def run_data_command(argv):
    """Run data subcommand for fetching research datasets."""
    import argparse
    c = Colors

    parser = argparse.ArgumentParser(
        prog="opendraft data",
        description="Fetch research data from World Bank, Eurostat, or Our World in Data"
    )
    parser.add_argument("provider", choices=["worldbank", "eurostat", "owid", "search", "list"],
                        help="Data provider or 'list' to show providers")
    parser.add_argument("query", nargs="?", help="Indicator code or dataset name")
    parser.add_argument("--countries", "-c", default="all",
                        help="Countries for World Bank (semicolon-separated codes, e.g., 'USA;DEU;FRA')")
    parser.add_argument("--start", "-s", type=int, help="Start year")
    parser.add_argument("--end", "-e", type=int, help="End year")
    parser.add_argument("--output", "-o", type=Path, default=Path.cwd(),
                        help="Output directory (default: current directory)")

    args = parser.parse_args(argv)

    print()
    print(f"  {c.BOLD}Data Fetch{c.RESET}")
    print(f"  {c.GRAY}{'─' * 40}{c.RESET}")

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from utils.data_fetch import DataFetcher, SDMX_PROVIDERS

        # List providers
        if args.provider == "list":
            print(f"\n  {c.BOLD}Available Data Providers{c.RESET}\n")
            for key, info in SDMX_PROVIDERS.items():
                print(f"  {c.CYAN}{key:12}{c.RESET} {info['name']} - {info['description']}")
            print()
            print(f"  {c.GRAY}Examples:{c.RESET}")
            print(f"    opendraft data search GDP")
            print(f"    opendraft data worldbank NY.GDP.MKTP.CD --countries USA;DEU;FRA")
            print(f"    opendraft data owid covid-19")
            print(f"    opendraft data eurostat nama_10_gdp")
            print()
            return 0

        if not args.query:
            print(f"\n  {c.RED}✗{c.RESET} Query/indicator required for provider '{args.provider}'\n")
            return 1

        fetcher = DataFetcher(args.output)

        print(f"  {c.GRAY}Provider:{c.RESET} {args.provider}")
        print(f"  {c.GRAY}Query:{c.RESET}    {args.query}")
        print()
        print(f"  {c.PURPLE}⣾{c.RESET} Fetching data...")

        # Execute fetch
        if args.provider == "worldbank":
            result = fetcher.fetch_worldbank(
                args.query,
                countries=args.countries,
                start_year=args.start,
                end_year=args.end,
            )
        elif args.provider == "eurostat":
            result = fetcher.fetch_eurostat(
                args.query,
                start_period=str(args.start) if args.start else None,
                end_period=str(args.end) if args.end else None,
            )
        elif args.provider == "owid":
            result = fetcher.fetch_owid(args.query)
        elif args.provider == "search":
            result = fetcher.search_worldbank(args.query)
        else:
            result = {"status": "error", "message": f"Unknown provider: {args.provider}"}

        print()

        if result.get("status") == "success":
            print(f"  {c.GREEN}✓{c.RESET} {result.get('message', 'Success')}")
            print()

            if "file_path" in result:
                print(f"  {c.GRAY}Saved to:{c.RESET} {result['file_path']}")
            if "rows" in result:
                print(f"  {c.GRAY}Rows:{c.RESET}     {result['rows']:,}")
            if "countries" in result:
                print(f"  {c.GRAY}Countries:{c.RESET} {result['countries']}")
            if "years" in result:
                print(f"  {c.GRAY}Years:{c.RESET}    {result['years']}")
            if "columns" in result:
                print(f"  {c.GRAY}Columns:{c.RESET}  {', '.join(result['columns'][:5])}...")
            if "indicators" in result:
                print()
                print(f"  {c.BOLD}Matching Indicators:{c.RESET}")
                for ind in result['indicators'][:10]:
                    print(f"    {c.CYAN}{ind['code']:25}{c.RESET} {ind['name'][:50]}")
                if len(result['indicators']) > 10:
                    print(f"    {c.GRAY}... and {len(result['indicators']) - 10} more{c.RESET}")
            print()
            return 0
        else:
            print(f"  {c.RED}✗{c.RESET} {result.get('message', 'Unknown error')}\n")
            return 1

    except Exception as e:
        print_friendly_error(e)
        return 1


def run_tool_command(argv):
    """Agent tool interface: `opendraft tool <name> --root <dir> --args '<json>'`.

    Machine-facing: prints exactly one JSON envelope line on stdout.
    Exit codes: 0 ok, 1 tool-level failure (or partial errors on list), 2 usage error.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="opendraft tool",
        description="Agent tool interface. Prints one JSON envelope line on stdout.",
    )
    parser.add_argument("name", help="Tool name, or 'list' to list all tools")
    parser.add_argument("--root", type=Path, default=Path.cwd(),
                        help="Output directory the tool operates on (default: cwd)")
    parser.add_argument("--args", default="{}",
                        help="Tool arguments as a JSON object string (default '{}')")
    parser.add_argument("--args-file", type=Path,
                        help="Read tool arguments from a JSON file instead of --args")
    parser.add_argument("--schema", action="store_true",
                        help="Print the tool's input JSON schema instead of running it")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).parent.parent))

    def _emit(payload, code):
        # UTF-8 bytes to stdout regardless of console codepage — this is a machine contract.
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        except (AttributeError, OSError):
            print(json.dumps(payload, ensure_ascii=False))
        return code

    try:
        from agent_tools import registry
    except Exception as e:
        return _emit({"ok": False, "error": f"tool layer unavailable: {type(e).__name__}: {e}"}, 2)

    if args.name == "list":
        available, errors = registry.list_tools()
        return _emit({"ok": True, "data": {"tools": available, "errors": errors}},
                     0 if not errors else 1)

    try:
        spec = registry.get_tool(args.name)
    except KeyError as e:
        return _emit({"ok": False, "error": str(e)}, 2)
    except Exception as e:
        return _emit({"ok": False, "error": f"tool failed to load: {type(e).__name__}: {e}"}, 2)

    if args.schema:
        return _emit({"ok": True, "data": {
            "name": spec.name, "description": spec.description, "input_schema": spec.input_schema,
        }}, 0)

    try:
        raw = args.args_file.read_text(encoding="utf-8") if args.args_file else args.args
    except OSError as e:
        return _emit({"ok": False, "error": f"cannot read args file: {e}"}, 2)
    try:
        tool_args = json.loads(raw)
    except json.JSONDecodeError as e:
        return _emit({"ok": False, "error": f"--args is not valid JSON: {e}"}, 2)
    if not isinstance(tool_args, dict):
        return _emit({"ok": False, "error": "--args must be a JSON object"}, 2)

    try:
        result = spec.func(tool_args, args.root)
    except Exception as e:
        result = {"ok": False, "error": f"{type(e).__name__}: {e}", "is_retryable": False}
    return _emit(result, 0 if result.get("ok") else 1)


def run_harness_command(argv):
    """Agent harness driver: `opendraft harness section|review --root DIR ...`.

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
    p_section.add_argument("--root", type=Path, required=True,
                           help="Paper output directory (the agent's working root)")
    p_section.add_argument("--section", required=True,
                           help="Section to write, e.g. literature_review")
    p_review = sub.add_parser("review", help="Global cross-section review with the pi agent loop")
    p_review.add_argument("--root", type=Path, required=True,
                          help="Paper output directory (the agent's working root)")
    for p in (p_section, p_review):
        p.add_argument("--model", default=None,
                       help="pi model pattern (default: env PI_MODEL or minimax-cn/MiniMax-M3)")
        p.add_argument("--max-cost", type=float, default=1.0,
                       help="Budget in USD before steering wrap-up (default 1.0)")
        p.add_argument("--max-turns", type=int, default=40,
                       help="Max agent turns before steering wrap-up (default 40)")

    args = parser.parse_args(argv)
    if args.harness_cmd not in ("section", "review"):
        parser.print_help()
        return 2

    def _progress(msg):
        print(f"[harness] {msg}", file=sys.stderr, flush=True)

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from harness.driver import BudgetConfig, PiDriver

    if args.harness_cmd == "section":
        from agent_tools.common import SECTION_FILES
        if args.section not in SECTION_FILES:
            _progress(f"error: unknown section '{args.section}' "
                      f"(valid: {', '.join(sorted(SECTION_FILES))})")
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
        f"done: ok={result.ok} reason={result.reason} "
        f"budget_exceeded={result.budget_exceeded}"
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
                sync_checkpoint_section(driver.root, args.section,
                                        section_file.read_text(encoding="utf-8"))
            verdict = score_run({"scope": "section", "section": args.section}, driver.root)
            acceptance = verdict.get("data") if verdict.get("ok") else {
                "passed": False, "error": verdict.get("error"),
            }
            _progress(
                f"acceptance: passed={acceptance.get('passed')} "
                f"words={acceptance.get('words')} citations={len(acceptance.get('citations') or [])}"
            )
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


def main():
    """Main CLI entry point."""
    import argparse

    # Handle subcommands before argparse (they have their own parsers)
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == 'setup':
            if run_setup():
                print()
                print(f"  {Colors.BOLD}Verifying installation...{Colors.RESET}")
                print()
                from opendraft.verify import verify_installation
                return verify_installation()
            return 1
        if cmd == 'verify':
            from opendraft.verify import verify_installation
            return verify_installation()
        if cmd == 'tldr':
            return run_tldr_command(sys.argv[2:])
        if cmd == 'digest':
            return run_digest_command(sys.argv[2:])
        if cmd == 'revise':
            return run_revise_command(sys.argv[2:])
        if cmd == 'data':
            return run_data_command(sys.argv[2:])
        if cmd == 'tool':
            return run_tool_command(sys.argv[2:])
        if cmd == 'harness':
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
  opendraft tldr <file>        Generate 5-bullet TL;DR for any paper
  opendraft digest <file>      Generate 60-second audio digest
  opendraft revise <folder> "instructions"   Revise existing draft
  opendraft data <provider> <query>          Fetch research datasets

{Colors.BOLD}Examples:{Colors.RESET}
  opendraft tool list
  opendraft harness section --root ./paper --section literature_review
  opendraft harness review --root ./paper
  opendraft tldr paper.pdf
  opendraft digest paper.pdf --voice josh
  opendraft revise ./output "make the intro longer"
  opendraft data worldbank NY.GDP.MKTP.CD --countries USA;DEU

{Colors.GRAY}https://opendraft.xyz{Colors.RESET}
        """
    )

    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"opendraft {__version__}"
    )

    args = parser.parse_args()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
