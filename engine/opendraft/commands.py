#!/usr/bin/env python3
"""
ABOUTME: Human-facing content subcommands: tldr, digest, revise, data.
ABOUTME: Each is argparse + console output + friendly-error rendering; the
ABOUTME: heavy lifting lives in tldr.py / digest.py / utils.revise / utils.data_fetch.
"""

import argparse
import os
import sys
from pathlib import Path

from opendraft.ui import Colors, get_api_key, has_api_key, print_friendly_error


def _c():
    return Colors


def run_tldr_command(argv):
    """Run TL;DR subcommand."""
    c = Colors

    parser = argparse.ArgumentParser(
        prog="opendraft tldr", description="Generate 5-bullet TL;DR summary for any paper"
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
        sys.path.insert(0, str(Path(__file__).parent.parent))
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
        for line in tldr.split("\n"):
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
    c = Colors

    parser = argparse.ArgumentParser(
        prog="opendraft digest", description="Generate 60-second audio digest for any paper"
    )
    parser.add_argument("document", help="Path to document (PDF, MD, or TXT)")
    parser.add_argument("--output", "-o", help="Output directory")
    parser.add_argument(
        "--voice",
        default="rachel",
        choices=["rachel", "adam", "josh", "elli", "bella"],
        help="ElevenLabs voice (default: rachel)",
    )
    parser.add_argument(
        "--no-audio", action="store_true", help="Skip audio generation (script only)"
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
        sys.path.insert(0, str(Path(__file__).parent.parent))
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
    c = Colors

    parser = argparse.ArgumentParser(
        prog="opendraft revise", description="Revise an existing draft with AI assistance"
    )
    parser.add_argument("target", help="Path to draft folder or markdown file")
    parser.add_argument(
        "instructions", help="Revision instructions (e.g., 'make the introduction longer')"
    )
    parser.add_argument(
        "--model",
        "-m",
        default="gemini-3-flash-preview",
        help="Gemini model to use (default: gemini-3-flash-preview)",
    )

    args = parser.parse_args(argv)
    target_path = Path(args.target)

    if not target_path.exists():
        print(f"\n  {c.RED}✗{c.RESET} Path not found: {target_path}\n")
        return 1

    print()
    print(f"  {c.BOLD}Revise{c.RESET}")
    print(f"  {c.GRAY}{'─' * 40}{c.RESET}")
    print(f"  {c.GRAY}Target:{c.RESET}       {target_path}")
    print(
        f"  {c.GRAY}Instructions:{c.RESET} {args.instructions[:50]}{'...' if len(args.instructions) > 50 else ''}"
    )
    print()

    # Ensure API key is set
    if not has_api_key():
        print(f"  {c.YELLOW}!{c.RESET} Run {c.BOLD}opendraft setup{c.RESET} first.\n")
        return 1

    if not os.getenv("GOOGLE_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = get_api_key()

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from utils.revise import find_draft_in_folder, revise_draft

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
        delta_color = c.GREEN if result["delta"] >= 0 else c.RED
        delta_sign = "+" if result["delta"] >= 0 else ""
        print(
            f"  {c.GRAY}Quality:{c.RESET} {result['score_before']} → {result['score_after']} ({delta_color}{delta_sign}{result['delta']}{c.RESET})"
        )
        print(
            f"  {c.GRAY}Words:{c.RESET}   {result['word_count_before']:,} → {result['word_count']:,}"
        )

        print()
        print(f"  {c.GRAY}Files:{c.RESET}")
        print(f"    {c.CYAN}📝{c.RESET} {result['md_path']}")
        if result["pdf_path"]:
            print(f"    {c.CYAN}📄{c.RESET} {result['pdf_path']}")
        if result["docx_path"]:
            print(f"    {c.CYAN}📑{c.RESET} {result['docx_path']}")
        print()

        return 0

    except Exception as e:
        print_friendly_error(e)
        return 1


def run_data_command(argv):
    """Run data subcommand for fetching research datasets."""
    c = Colors

    parser = argparse.ArgumentParser(
        prog="opendraft data",
        description="Fetch research data from World Bank, Eurostat, or Our World in Data",
    )
    parser.add_argument(
        "provider",
        choices=["worldbank", "eurostat", "owid", "search", "list"],
        help="Data provider or 'list' to show providers",
    )
    parser.add_argument("query", nargs="?", help="Indicator code or dataset name")
    parser.add_argument(
        "--countries",
        "-c",
        default="all",
        help="Countries for World Bank (semicolon-separated codes, e.g., 'USA;DEU;FRA')",
    )
    parser.add_argument("--start", "-s", type=int, help="Start year")
    parser.add_argument("--end", "-e", type=int, help="End year")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path.cwd(),
        help="Output directory (default: current directory)",
    )

    args = parser.parse_args(argv)

    print()
    print(f"  {c.BOLD}Data Fetch{c.RESET}")
    print(f"  {c.GRAY}{'─' * 40}{c.RESET}")

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from utils.data_fetch import SDMX_PROVIDERS, DataFetcher

        # List providers
        if args.provider == "list":
            print(f"\n  {c.BOLD}Available Data Providers{c.RESET}\n")
            for key, info in SDMX_PROVIDERS.items():
                print(f"  {c.CYAN}{key:12}{c.RESET} {info['name']} - {info['description']}")
            print()
            print(f"  {c.GRAY}Examples:{c.RESET}")
            print("    opendraft data search GDP")
            print("    opendraft data worldbank NY.GDP.MKTP.CD --countries USA;DEU;FRA")
            print("    opendraft data owid covid-19")
            print("    opendraft data eurostat nama_10_gdp")
            print()
            return 0

        if not args.query:
            print(
                f"\n  {c.RED}✗{c.RESET} Query/indicator required for provider '{args.provider}'\n"
            )
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
                for ind in result["indicators"][:10]:
                    print(f"    {c.CYAN}{ind['code']:25}{c.RESET} {ind['name'][:50]}")
                if len(result["indicators"]) > 10:
                    print(f"    {c.GRAY}... and {len(result['indicators']) - 10} more{c.RESET}")
            print()
            return 0
        else:
            print(f"  {c.RED}✗{c.RESET} {result.get('message', 'Unknown error')}\n")
            return 1

    except Exception as e:
        print_friendly_error(e)
        return 1
