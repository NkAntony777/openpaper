# Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks (Lewis et al., 2020) — {cite_001}

## Summary
RAG couples a query encoder + Wikipedia dense index (DPR) with a BART generator. Two variants: RAG-Sequence (same document for the whole answer) and RAG-Token (different documents per token). The retriever is trained with the QA supervision signal through the generator.

## Key findings
- Large gains over parametric-only T5/BART and over extractive DPR+reader pipelines on Natural Questions, TriviaQA, WebQuestions.
- Answers can be updated by swapping the index without retraining — crucial for fresh domains.
- Generation remains fluent even when retrieval fails (the model falls back on parametric knowledge).

## Relevance to our topic
The foundational architecture every domain QA system derives from; its parametric/non-parametric split is the baseline design choice.
