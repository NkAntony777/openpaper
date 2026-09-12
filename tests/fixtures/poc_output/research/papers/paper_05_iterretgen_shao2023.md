# Iterative Retrieval-Generation Synergy (Shao et al., 2023) — {cite_011}

## Summary
ITER-RETGEN alternates: draft an answer with the current context, use the draft as a new query, retrieve again, regenerate. Fixed small number of iterations.

## Key findings
- Largest gains on multi-hop questions (HotpotQA-style) and on questions requiring paraphrase of domain terminology.
- Works with frozen retriever and frozen generator — no retraining.

## Relevance
Domain QA often needs iterative refinement because the user's first query doesn't match corpus vocabulary.
