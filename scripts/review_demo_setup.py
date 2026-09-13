import json
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).parent.parent
PY = REPO / ".venv" / "Scripts" / "python.exe"
POC = Path(r"C:/Users/Anthony/AppData/Local/Temp/opendraft_poc_iq8ano7r/out")

root = Path(tempfile.mkdtemp(prefix="openpaper_review_demo_")) / "out"
shutil.copytree(POC, root)

# Demo-scale targets: the fixture's research_paper ranges imply ~560-word floors;
# our hand-written demo sections are intentionally shorter.
ckpt_path = root / "checkpoint.json"
ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
ckpt["word_targets"]["introduction"] = "300-500"
ckpt["word_targets"]["methodology"] = "300-500"
ckpt_path.write_text(json.dumps(ckpt, indent=2, ensure_ascii=False), encoding="utf-8")

print("demo root:", root, flush=True)


def tool(name, args):
    r = subprocess.run(
        [
            str(PY),
            "-c",
            f"import sys; sys.path.insert(0, r'{REPO / 'engine'}'); "
            f"from opendraft.cli import run_tool_command; "
            f"sys.exit(run_tool_command([{name!r}, '--root', r'{root}', '--args-file', r'{args}']))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO,
    )
    line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "{}"
    env = json.loads(line)
    print(
        f"[{'OK' if env.get('ok') else 'FAIL'}] {name} "
        f"{'' if env.get('ok') else env.get('error', '')[:200]}",
        flush=True,
    )
    return env


def tmp_args(obj):
    f = root / f".args_{next(counter)}.json"
    f.write_text(json.dumps(obj), encoding="utf-8")
    return f


counter = iter(range(100))

INTRO = """## 1 Introduction

Domain experts increasingly turn to question-answering systems over proprietary
technical documentation, yet general-purpose language models remain unreliable
on niche factual queries: they hallucinate references, miss recent findings, and
cannot cite their sources. These failures are costly in professional settings,
where an unsupported claim can mislead a diagnosis, a design review, or a
compliance decision. The root cause is architectural: a parametric model
compresses its training corpus into weights and cannot be inspected, updated, or
held accountable at the level of individual claims.

Retrieval-augmented generation (RAG), introduced by Lewis et al. {cite_001},
decomposes the problem into a retriever over an external corpus and a parametric
generator, and has become the dominant architecture for knowledge-intensive NLP.

For domain-specific question answering, however, the design space is contested.
Dense retrievers such as DPR {cite_002} outperform lexical baselines on open-domain
benchmarks, but domain corpora exhibit terminology mismatch between short expert
queries and indexed text, which both sparse and dense methods struggle with.
Adaptive methods — learning when to retrieve {cite_006}, correcting bad retrieval
{cite_007}, or interleaving drafting and search {cite_011} — promise robustness,
but their benefit in professional domains is under-measured.

This paper makes three contributions. First, we present a controlled benchmark
comparing sparse and dense retrievers on domain-specific QA with accuracy,
citation precision, and latency as metrics. Second, we propose an iterative
retrieval-generation method that alternates drafting with retrieval rounds
{cite_011}, targeting the terminology-mismatch regime. Third, we provide an
analysis of the latency-cost trade-offs of adaptive retrieval in deployment
settings, an evaluation dimension the literature has largely omitted {cite_012}.

The remainder of the paper reviews the foundations and recent advances in
retrieval-augmented generation, details our methodology, reports results,
discusses threats to validity, and concludes with directions for future work.

Our benchmark design follows best practices for retrieval evaluation: queries are
sampled from real expert support tickets, corpus documents are deduplicated by
DOI, and gold answers are validated by domain experts before inclusion. This
setup mirrors the conditions under which domain QA systems are actually deployed,
where queries are short, vocabulary is specialized, and answer freshness matters.

The paper is organized as follows. Section 2 reviews foundations and recent
advances, from dense passage retrieval to adaptive and corrective retrieval.
Section 3 describes the benchmark protocol and the proposed system. Section 4
reports the main comparison and ablations. Section 5 discusses what the results
mean for practitioners, and Section 6 concludes.
"""

INTRO_SUMMARY = (
    "Introduces domain QA problem and RAG baseline {cite_001}; contributions: "
    "(1) sparse-vs-dense retriever benchmark, (2) an ITERATIVE retrieval-generation "
    "method {cite_011}, (3) latency-cost analysis. Uses standard term RAG throughout."
)

METHOD = """## 3 Methodology

### 3.1 The NRQA pipeline

We implement NRQA (Neural Retrieval Question Answering), our end-to-end system
for domain-specific question answering. NRQA differs from classical
retrieval-augmented generation in two design decisions, both chosen for
operational simplicity in deployment.

First, retrieval is exclusively sparse: we adopt BM25 {cite_009} over the domain
corpus and deliberately avoid dense encoders. In our pilot studies, dense
retrievers such as DPR {cite_002} showed no measurable benefit over BM25 on our
domain queries, consistent with reports that embedding-based methods degrade
under terminology mismatch. A sparse index also removes GPU inference from the
retrieval path, cutting serving cost substantially.

Second, retrieval is single-shot: each question is answered from one retrieval
round of the top-20 passages. We do not interleave drafting and retrieval, and
we do not adapt retrieval depth per question, because a fixed pipeline is easier
to monitor in production. All passages are fused in a single decoder pass,
following the fusion-in-decoder pattern {cite_004}.

### 3.2 Datasets and metrics

We evaluate on a domain QA set of 2,400 expert questions over 18,000 technical
documents. Metrics: answer accuracy (exact match against gold answers), citation
precision (fraction of generated citations supporting the claim, judged by two
annotators), and p95 latency per question.

### 3.3 Baselines and ablation

Baselines: closed-book generation (no retrieval); BM25 + extractive reader; and
our full NRQA pipeline. Ablations vary the number of retrieved passages and the
fusion strategy {cite_004}. Every configuration is run at three random seeds.
Statistical significance is assessed with bootstrap confidence intervals over the
question set, and we report effect sizes rather than only p-values, following
recent recommendations for retrieval evaluations.

### 3.4 Implementation details

The corpus is indexed with a standard inverted index; the generator is a 7B
instruction-tuned decoder fine-tuned on 4,000 domain QA pairs. Training used
learning rate 1e-5, batch size 32, three epochs. Inference uses beam search with
beam width 4 and a 512-token budget per answer.

### 3.5 Reproducibility

All indices, splits, prompts, and evaluation scripts are versioned, and every
reported number is produced by a single deterministic pipeline run. Annotation
guidelines for citation precision are included in the appendix, and inter-annotator
agreement exceeded 0.81 Cohen's kappa on a 200-question overlap sample.

All experiments run on a single node with 64 GB RAM and one consumer GPU; the
sparse index builds in under twenty minutes and fits in memory alongside the
generator, which keeps the full system within the resource envelope of a small
engineering team without dedicated serving infrastructure.
"""

METHOD_SUMMARY = (
    "Presents NRQA pipeline: EXCLUSIVELY BM25 sparse retrieval {cite_009}, dense "
    "retrievers (DPR {cite_002}) found no benefit; SINGLE-SHOT retrieval (no "
    "interleaving), fusion-in-decoder {cite_004}. Metrics: accuracy, citation "
    "precision, latency; baselines and ablations described."
)

tool(
    "write_section",
    tmp_args(
        {
            "section": "introduction",
            "content": INTRO,
            "citations_used": [
                "cite_001",
                "cite_002",
                "cite_006",
                "cite_007",
                "cite_011",
                "cite_012",
            ],
            "summary": INTRO_SUMMARY,
        }
    ),
)
tool(
    "write_section",
    tmp_args(
        {
            "section": "methodology",
            "content": METHOD,
            "citations_used": ["cite_009", "cite_002", "cite_004"],
            "summary": METHOD_SUMMARY,
        }
    ),
)
tool("score_draft", tmp_args({"scope": "section", "section": "introduction"}))
tool("score_draft", tmp_args({"scope": "section", "section": "methodology"}))

for f in root.glob(".args_*.json"):
    f.unlink()

status = json.loads((root / "section_status.json").read_text(encoding="utf-8"))
print("ledger sections:", list(status.get("sections", {})), flush=True)
print(
    "ledger intro:",
    {k: status["sections"]["introduction"].get(k) for k in ("status", "words", "passed")},
    flush=True,
)
print("SETUP DONE", flush=True)
print(root, flush=True)
