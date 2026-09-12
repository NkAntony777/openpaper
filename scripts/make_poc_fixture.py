#!/usr/bin/env python3
"""
ABOUTME: Build the M1 PoC fixture: a realistic post-research output directory
ABOUTME: (bibliography + paper notes + outline + checkpoint) for a fixed topic,
ABOUTME: so the harness PoC can exercise the writing loop without burning tokens
ABOUTME: on the research phase. Re-runnable: overwrites the fixture directory.
"""

import json
import shutil
from pathlib import Path

TOPIC = "The Impact of Retrieval-Augmented Generation on Domain-Specific Question Answering"
LEVEL = "research_paper"
OUT = Path(__file__).parent.parent / "tests" / "fixtures" / "poc_output"

CITATIONS = [
    {"id": "cite_001", "authors": ["Lewis", "Perez", "Piktus"], "year": 2020,
     "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
     "journal": "Advances in Neural Information Processing Systems (NeurIPS)",
     "source_type": "journal", "language": "english",
     "url": "https://arxiv.org/abs/2005.11401",
     "abstract": "Introduces RAG: combining a parametric seq2seq generator with a non-parametric dense passage retriever, showing large gains on knowledge-intensive tasks like open-domain QA."},
    {"id": "cite_002", "authors": ["Karpukhin", "Ogunrinde", "Min"], "year": 2020,
     "title": "Dense Passage Retrieval for Open-Domain Question Answering",
     "journal": "Proceedings of EMNLP",
     "source_type": "journal", "language": "english",
     "url": "https://arxiv.org/abs/2004.04906",
     "abstract": "Shows that dense encoders trained with dual objectives outperform BM25 for open-domain QA passage retrieval, becoming the standard retriever for RAG pipelines."},
    {"id": "cite_003", "authors": ["Guu", "Lee", "Tung"], "year": 2020,
     "title": "REALM: Retrieval-Augmented Language Model Pre-Training",
     "journal": "Proceedings of ICML",
     "source_type": "journal", "language": "english",
     "url": "https://arxiv.org/abs/2002.08909",
     "abstract": "Pre-trains a language model with a latent knowledge retriever end-to-end, demonstrating that retrieval can be integrated into pretraining rather than added at inference time."},
    {"id": "cite_004", "authors": ["Izacard", "Grave"], "year": 2021,
     "title": "Leveraging Passage Retrieval with Generative Models for Open Domain Question Answering",
     "journal": "Proceedings of EACL",
     "source_type": "journal", "language": "english",
     "url": "https://arxiv.org/abs/2007.01282",
     "abstract": "Fusion-in-Decoder: scores many retrieved passages jointly in the decoder, achieving state-of-the-art open-domain QA with a reader much smaller than generator-only models."},
    {"id": "cite_005", "authors": ["Izacard", "Lewis", "Lomeli"], "year": 2022,
     "title": "Atlas: Few-shot Learning with Retrieval Augmented Language Models",
     "journal": "Journal of Machine Learning Research",
     "source_type": "journal", "language": "english",
     "url": "https://arxiv.org/abs/2208.03299",
     "abstract": "A large retrieval-augmented model that excels at few-shot knowledge-intensive tasks, indexing billions of documents and probing the effect of model scale and index size."},
    {"id": "cite_006", "authors": ["Asai", "Wu", "Wang"], "year": 2023,
     "title": "Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection",
     "journal": "Proceedings of ICLR",
     "source_type": "journal", "language": "english",
     "url": "https://arxiv.org/abs/2310.11511",
     "abstract": "Trains a single model to adaptively decide when to retrieve and to critique its own generations with reflection tokens, improving factuality and citation precision."},
    {"id": "cite_007", "authors": ["Yan", "Xiong", "Song"], "year": 2024,
     "title": "Corrective Retrieval Augmented Generation",
     "journal": "Proceedings of ACL",
     "source_type": "journal", "language": "english",
     "url": "https://arxiv.org/abs/2401.15884",
     "abstract": "CRAG adds a lightweight retrieval evaluator that judges retrieved documents and triggers corrective actions (decompose, filter, web search) when retrieval quality is poor."},
    {"id": "cite_008", "authors": ["Reimers", "Gurevych"], "year": 2019,
     "title": "Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks",
     "journal": "Proceedings of EMNLP",
     "source_type": "journal", "language": "english",
     "url": "https://arxiv.org/abs/1908.10084",
     "abstract": "Siamese network fine-tuning of BERT produces semantically meaningful sentence embeddings, the backbone of many dense retrieval encoders used in RAG."},
    {"id": "cite_009", "authors": ["Robertson", "Zaragoza"], "year": 2009,
     "title": "The Probabilistic Relevance Framework: BM25 and Beyond",
     "journal": "Foundations and Trends in Information Retrieval",
     "source_type": "journal", "language": "english",
     "abstract": "The canonical reference for BM25, the sparse lexical baseline against which all dense retrievers in RAG systems are compared."},
    {"id": "cite_010", "authors": ["Izacard", "Caron", "Hosseini"], "year": 2021,
     "title": "Unsupervised Dense Information Retrieval with Contrastive Learning",
     "journal": "Transactions on Machine Learning Research",
     "source_type": "journal", "language": "english",
     "url": "https://arxiv.org/abs/2112.09118",
     "abstract": "Contriever: contrastive unsupervised pretraining yields dense retrievers competitive with BM25 without any labeled pairs, relevant when domain QA lacks training data."},
    {"id": "cite_011", "authors": ["Shao", "Gong", "Shen"], "year": 2023,
     "title": "Enhancing Retrieval-Augmented Large Language Models with Iterative Retrieval-Generation Synergy",
     "journal": "Proceedings of EMNLP",
     "source_type": "journal", "language": "english",
     "url": "https://arxiv.org/abs/2305.15294",
     "abstract": "ITER-RETGEN interleaves retrieval and generation so that model drafts inform subsequent retrieval, improving multi-hop and domain-specific QA over single-shot RAG."},
    {"id": "cite_012", "authors": ["Gao", "Xiong", "Gao"], "year": 2023,
     "title": "Retrieval-Augmented Generation for Large Language Models: A Survey",
     "journal": "arXiv preprint arXiv:2312.10997",
     "source_type": "journal", "language": "english",
     "url": "https://arxiv.org/abs/2312.10997",
     "abstract": "Comprehensive survey organizing RAG by naive/advanced/modular paradigms and identifying tuning, evaluation, and domain adaptation as open challenges."},
]

PAPER_NOTES = {
    "paper_01_rag_lewis2020.md": """# Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks (Lewis et al., 2020) — {cite_001}

## Summary
RAG couples a query encoder + Wikipedia dense index (DPR) with a BART generator. Two variants: RAG-Sequence (same document for the whole answer) and RAG-Token (different documents per token). The retriever is trained with the QA supervision signal through the generator.

## Key findings
- Large gains over parametric-only T5/BART and over extractive DPR+reader pipelines on Natural Questions, TriviaQA, WebQuestions.
- Answers can be updated by swapping the index without retraining — crucial for fresh domains.
- Generation remains fluent even when retrieval fails (the model falls back on parametric knowledge).

## Relevance to our topic
The foundational architecture every domain QA system derives from; its parametric/non-parametric split is the baseline design choice.
""",
    "paper_02_dpr_karpukhin2020.md": """# Dense Passage Retrieval for Open-Domain QA (Karpukhin et al., 2020) — {cite_002}

## Summary
Dual-encoder BERT retriever trained with in-batch negatives + QA pairs. At query time, encodes the question, retrieves top-k Wikipedia passages from an MIPS index.

## Key findings
- +9-19 absolute points over BM25 on Natural Questions open-domain retrieval.
- Retriever quality dominates end QA performance; generator can't compensate for missed evidence.
- Training data scale matters more than architecture tweaks.

## Relevance
Established dense retrieval as the default; BM25 ({cite_009}) remains the hard baseline on out-of-domain queries.
""",
    "paper_03_selfrag_asai2023.md": """# Self-RAG (Asai et al., 2023) — {cite_006}

## Summary
Adds reflection tokens so one Llama-style model learns four decisions: retrieve or not, relevance grade, support grade, utility grade. Trained with critique-augmented synthetic data + gold QA supervision.

## Key findings
- Adaptive retrieval saves ~30% of retrieval calls with no accuracy loss on some tasks.
- Citation precision improves markedly — the model learns to prefer generations grounded in retrieved text.
- Outperforms standard RAG and ChatGPT-like closed-book models on factuality benchmarks.

## Relevance
Directly addresses when retrieval helps in domain QA: for well-known facts the parametric memory suffices, for niche domain facts retrieval is essential.
""",
    "paper_04_crag_yan2024.md": """# Corrective RAG (Yan et al., 2024) — {cite_007}

## Summary
Inserts a retrieval evaluator (small T5) that classifies retrieved docs as correct/ambiguous/incorrect; triggers corrective actions: decomposition, filtering, confidence-weighted fusion, or web fallback.

## Key findings
- Robustness on single-hop QA improves most when retrieval is noisy — exactly the domain QA regime where terminology mismatch between query and corpus is common.
- Plug-in design: evaluator attaches to any RAG pipeline.

## Relevance
Domain QA queries are short and jargon-heavy; retrieval quality fluctuates, making corrective mechanisms high-value.
""",
    "paper_05_iterretgen_shao2023.md": """# Iterative Retrieval-Generation Synergy (Shao et al., 2023) — {cite_011}

## Summary
ITER-RETGEN alternates: draft an answer with the current context, use the draft as a new query, retrieve again, regenerate. Fixed small number of iterations.

## Key findings
- Largest gains on multi-hop questions (HotpotQA-style) and on questions requiring paraphrase of domain terminology.
- Works with frozen retriever and frozen generator — no retraining.

## Relevance
Domain QA often needs iterative refinement because the user's first query doesn't match corpus vocabulary.
""",
    "paper_06_atlas_izacard2022.md": """# Atlas (Izacard et al., 2022) — {cite_005}

## Summary
11B retrieval-augmented encoder-decoder (T5 + Contriever-style index) pretrained with masked-language modeling over the index and fine-tuned per task.

## Key findings
- Strong few-shot: matches or beats GPT-3-style models at 1/10th the parameters on knowledge tasks.
- Scaling the index helps until a point; scaling the model helps more on reasoning-heavy tasks.

## Relevance
Evidence that retrieval augmentation reduces the model-size requirement for domain QA — a cost argument.
""",
}

COMBINED_RESEARCH = """# Combined Research — {topic}

## Theme 1: The retrieval/generation split
RAG ({cite_001}) established the parametric/non-parametric decomposition; REALM ({cite_003}) pushed retrieval into pretraining; Atlas ({cite_005}) scaled both. Consensus: retrieval should be treated as a replaceable component, not baked into weights.

## Theme 2: Retriever quality is the bottleneck
DPR ({cite_002}) beat BM25 ({cite_009}) on in-domain benchmarks, but unsupervised Contriever ({cite_010}) and SBERT ({cite_008}) embeddings remain competitive when labeled pairs are scarce — common in domain QA. Missed evidence cannot be compensated by the generator ({cite_002}).

## Theme 3: Adaptive and corrective retrieval
Self-RAG ({cite_006}) learns when to retrieve; CRAG ({cite_007}) detects bad retrieval and corrects it; ITER-RETGEN ({cite_011}) interleaves drafting and retrieval. Domain QA benefits disproportionately because query/corpus terminology mismatch is frequent.

## Theme 4: Evaluation gap
Surveys ({cite_012}) note that most RAG evaluations use open-domain Wikipedia QA; domain-specific factuality, citation precision, and latency trade-offs are under-measured.
""".replace("{topic}", TOPIC)

RESEARCH_GAPS = """# Research Gaps

1. **Domain terminology mismatch**: few works measure how query-side jargon affects retrieval recall in specialized domains; CRAG-style correction is promising but evaluated mostly on Wikipedia.
2. **Citation precision vs. fluency**: Self-RAG improves citation precision, but user studies in professional domains are missing.
3. **Latency-cost trade-off**: adaptive retrieval saves calls ({cite_006}) but end-to-end system cost analyses for deployment settings are absent.
"""

OUTLINE = f"""# Formatted Outline — {TOPIC}
**Level**: research_paper · **Style**: APA · **Language**: English

## 1. Introduction (600-800 words)
- Motivation: domain experts drowning in proprietary documentation
- Research question: when does retrieval augmentation beat long-context prompting for domain QA?
- Contributions preview

## 2. Literature Review (800-1,200 words)
- 2.1 Foundations of retrieval-augmented generation
- 2.2 Dense retrieval and embedding methods
- 2.3 Adaptive, corrective, and iterative retrieval
- 2.4 Synthesis: what domain QA demands

## 3. Methodology (600-800 words)
- System architecture; retriever choices (BM25, DPR, Contriever); datasets; metrics (accuracy, citation precision, latency)

## 4. Results (800-1,200 words)
- Main comparison table; ablation over retriever; adaptive retrieval savings

## 5. Discussion (600-800 words)
- Terminology mismatch findings; deployment trade-offs; threats to validity

## 6. Conclusion (400-600 words)
"""


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "research" / "papers").mkdir(parents=True)
    (OUT / "drafts").mkdir(parents=True)

    (OUT / "research" / "bibliography.json").write_text(json.dumps({
        "citations": CITATIONS,
        "metadata": {"total_citations": len(CITATIONS), "citation_style": "APA 7th",
                     "draft_language": "english", "extracted_date": "2026-02-20T00:00:00"},
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    for name, text in PAPER_NOTES.items():
        (OUT / "research" / "papers" / name).write_text(text, encoding="utf-8")
    (OUT / "research" / "combined_research.md").write_text(COMBINED_RESEARCH, encoding="utf-8")
    (OUT / "research" / "research_gaps.md").write_text(RESEARCH_GAPS, encoding="utf-8")
    (OUT / "drafts" / "00_formatted_outline.md").write_text(OUTLINE, encoding="utf-8")

    summary_lines = ["# Citation Summary", "",
                     "ONLY cite from this list, using `{cite_XXX}` placeholders.", ""]
    for c in CITATIONS:
        auth = ", ".join(c["authors"][:2]) + (" et al." if len(c["authors"]) > 2 else "")
        summary_lines.append(f"- `{c['id']}` — {auth} ({c['year']}). {c['title']}. *{c['journal']}*.")
    (OUT / "drafts" / "citation_summary.md").write_text("\n".join(summary_lines) + "\n",
                                                        encoding="utf-8")

    (OUT / "checkpoint.json").write_text(json.dumps({
        "version": "1.0", "completed_phase": "citations", "topic": TOPIC,
        "language": "en", "academic_level": LEVEL, "output_type": "full",
        "citation_style": "apa", "skip_validation": True, "verbose": True,
        "blurb": None, "research_brief": None,
        "word_targets": {
            "total": "3,000-5,000", "introduction": "600-800",
            "literature_review": "800-1,200", "methodology": "600-800",
            "results": "800-1,200", "discussion": "600-800",
            "conclusion": "400-600", "appendices": "0",
            "chapters": "3-4", "min_citations": 10,
        },
        "folders": {"root": str(OUT), "research": str(OUT / "research"),
                    "drafts": str(OUT / "drafts"), "exports": str(OUT / "exports")},
        "scout_output": "", "scribe_output": "", "signal_output": "",
        "scout_result": None, "architect_output": "", "formatter_output": OUTLINE,
        "citation_summary": "",
        "intro_output": "", "lit_review_output": "", "methodology_output": "",
        "results_output": "", "discussion_output": "", "body_output": "",
        "conclusion_output": "", "appendix_output": "",
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"fixture written to {OUT}")


if __name__ == "__main__":
    main()
