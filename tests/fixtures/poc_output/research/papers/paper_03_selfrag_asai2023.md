# Self-RAG (Asai et al., 2023) — {cite_006}

## Summary
Adds reflection tokens so one Llama-style model learns four decisions: retrieve or not, relevance grade, support grade, utility grade. Trained with critique-augmented synthetic data + gold QA supervision.

## Key findings
- Adaptive retrieval saves ~30% of retrieval calls with no accuracy loss on some tasks.
- Citation precision improves markedly — the model learns to prefer generations grounded in retrieved text.
- Outperforms standard RAG and ChatGPT-like closed-book models on factuality benchmarks.

## Relevance
Directly addresses when retrieval helps in domain QA: for well-known facts the parametric memory suffices, for niche domain facts retrieval is essential.
