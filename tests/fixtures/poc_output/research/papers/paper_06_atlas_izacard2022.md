# Atlas (Izacard et al., 2022) — {cite_005}

## Summary
11B retrieval-augmented encoder-decoder (T5 + Contriever-style index) pretrained with masked-language modeling over the index and fine-tuned per task.

## Key findings
- Strong few-shot: matches or beats GPT-3-style models at 1/10th the parameters on knowledge tasks.
- Scaling the index helps until a point; scaling the model helps more on reasoning-heavy tasks.

## Relevance
Evidence that retrieval augmentation reduces the model-size requirement for domain QA — a cost argument.
