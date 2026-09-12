# Combined Research — The Impact of Retrieval-Augmented Generation on Domain-Specific Question Answering

## Theme 1: The retrieval/generation split
RAG ({cite_001}) established the parametric/non-parametric decomposition; REALM ({cite_003}) pushed retrieval into pretraining; Atlas ({cite_005}) scaled both. Consensus: retrieval should be treated as a replaceable component, not baked into weights.

## Theme 2: Retriever quality is the bottleneck
DPR ({cite_002}) beat BM25 ({cite_009}) on in-domain benchmarks, but unsupervised Contriever ({cite_010}) and SBERT ({cite_008}) embeddings remain competitive when labeled pairs are scarce — common in domain QA. Missed evidence cannot be compensated by the generator ({cite_002}).

## Theme 3: Adaptive and corrective retrieval
Self-RAG ({cite_006}) learns when to retrieve; CRAG ({cite_007}) detects bad retrieval and corrects it; ITER-RETGEN ({cite_011}) interleaves drafting and retrieval. Domain QA benefits disproportionately because query/corpus terminology mismatch is frequent.

## Theme 4: Evaluation gap
Surveys ({cite_012}) note that most RAG evaluations use open-domain Wikipedia QA; domain-specific factuality, citation precision, and latency trade-offs are under-measured.
