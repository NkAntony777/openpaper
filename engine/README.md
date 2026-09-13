# OpenDraft Engine

The Python AI engine that powers thesis draft generation. Contains the 19-agent pipeline, citation research, and export functionality.

## Structure

```
engine/
├── draft_generator.py      # Main 19-stage pipeline orchestrator
├── config.py               # Model settings, API keys, rate limits
├── utils/
│   ├── llm_runtime.py     # Model setup + retrying agent LLM-call loop
│   ├── citation_research.py  # Scout: citation research orchestration
│   ├── api_citations/      # Citation APIs (CrossRef, Semantic Scholar)
│   ├── citation_*.py       # Citation management & validation
│   ├── export_professional.py  # PDF/DOCX export
│   ├── pdf_engines/        # Pandoc, WeasyPrint engines
│   └── deep_research.py    # Research phase utilities
├── prompts/
│   ├── 00_WORKFLOW.md      # Complete agent workflow
│   ├── 01_research/        # Deep Research, Scout, Scribe, Signal
│   ├── 02_structure/       # Architect, Citation Manager, Formatter
│   ├── 03_compose/         # Crafter, Thread, Narrator
│   ├── 04_validate/        # Skeptic, Verifier, Referee
│   ├── 05_refine/          # Citation Verifier, Voice, Entropy, Polish
│   └── 06_enhance/         # Abstract Generator, Enhancer
└── opendraft/              # CLI tools
```

## Usage

### Run Pipeline Directly

```bash
cd engine
python draft_generator.py --topic "Your research topic" --level master
```

### Academic Levels

| Level | Words | Chapters | Time |
|-------|-------|----------|------|
| research_paper | 3-5k | 3-4 | 5-10 min |
| bachelor | 10-15k | 5-7 | 8-15 min |
| master | 20-30k | 7-10 | 10-25 min |
| phd | 50-80k | 10-15 | 20-40 min |

## Environment Variables

Required in `.env` (project root):

```bash
GEMINI_API_KEY=your-key      # Required
PROXY_LIST=...               # Optional: for faster research
SCOUT_PARALLEL_WORKERS=32    # Optional: parallelism
```

## Dependencies

```bash
pip install -r requirements.txt
```
