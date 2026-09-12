# Corrective RAG (Yan et al., 2024) — {cite_007}

## Summary
Inserts a retrieval evaluator (small T5) that classifies retrieved docs as correct/ambiguous/incorrect; triggers corrective actions: decomposition, filtering, confidence-weighted fusion, or web fallback.

## Key findings
- Robustness on single-hop QA improves most when retrieval is noisy — exactly the domain QA regime where terminology mismatch between query and corpus is common.
- Plug-in design: evaluator attaches to any RAG pipeline.

## Relevance
Domain QA queries are short and jargon-heavy; retrieval quality fluctuates, making corrective mechanisms high-value.
