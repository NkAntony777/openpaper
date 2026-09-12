# Dense Passage Retrieval for Open-Domain QA (Karpukhin et al., 2020) — {cite_002}

## Summary
Dual-encoder BERT retriever trained with in-batch negatives + QA pairs. At query time, encodes the question, retrieves top-k Wikipedia passages from an MIPS index.

## Key findings
- +9-19 absolute points over BM25 on Natural Questions open-domain retrieval.
- Retriever quality dominates end QA performance; generator can't compensate for missed evidence.
- Training data scale matters more than architecture tweaks.

## Relevance
Established dense retrieval as the default; BM25 ({cite_009}) remains the hard baseline on out-of-domain queries.
