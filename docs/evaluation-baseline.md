# Retrieval evaluation — first baseline

Recorded 2026-09-07, end of Milestone 3. Reproduce with:

```bash
cd services/rag && .venv/Scripts/python.exe scripts/evaluate.py --user <id> --gold gold_sets/study-guide.jsonl --top-k 10
```

## Setup

| | |
|---|---|
| Corpus | `tests/fixtures/study-guide.pdf` — 20 sections, 20 chunks |
| Gold set | 19 hand-written questions, one section each |
| Embeddings | `gemini-embedding-2` @ 3072 dims |
| Reranker | `jina-reranker-v2-base-multilingual`, 25 candidates → 10 |
| Fusion | RRF, k=60 |

## Results

```
strategy             recall@5  precision@5          mrr       ndcg@5        hit@5       ms
------------------------------------------------------------------------------------------
bm25                   0.789        0.158        0.636        0.669        0.789       211
dense                  0.947*       0.189*       0.855        0.872*       0.947*    15026
hybrid                 0.947*       0.189*       0.761        0.801        0.947*    15452
hybrid_rerank          0.895        0.179        0.860*       0.868        0.895     37095
```

| strategy | recall@1 | recall@10 | MRR | NDCG@10 |
|---|---|---|---|---|
| bm25 | 0.526 | 0.842 | 0.636 | 0.687 |
| dense | 0.789 | **1.000** | 0.855 | **0.890** |
| hybrid | 0.632 | **1.000** | 0.761 | 0.819 |
| hybrid_rerank | **0.842** | 0.895 | **0.860** | 0.868 |

## What this says

**Hybrid does not beat dense here, and on ranking it is clearly worse.**
Identical recall@5 (0.947) but MRR drops from 0.855 to 0.761 and recall@1 from
0.789 to 0.632. Fusion is pulling weak BM25 matches up past good dense ones. On
a 20-chunk corpus BM25's IDF has almost no statistics to work with, so it
matches common words and RRF treats that rank as evidence.

**Reranking repairs the ranking but costs recall.** It lifts recall@1 from
0.632 to 0.842 and MRR to the best figure measured — but recall@10 falls from
1.000 to 0.895, meaning it discarded a chunk that hybrid had found. It also
takes 2.4× as long.

**Nothing here justifies hybrid+rerank over plain dense retrieval yet.** Dense
alone has the best NDCG@10 and perfect recall@10 at 40% of the latency.

## The bias in this gold set

The questions were written deliberately to avoid the vocabulary of the passages
that answer them — *"what happens to my data if the server loses power halfway
through saving"* shares no terms with the write-ahead-logging text. That is the
case dense retrieval should win, and it does.

**This measures one half of the question distribution.** A student asking
*"what is 3NF"* or *"define BCNF"* is the case BM25 wins, and this set contains
almost none of those. The honest reading is:

- dense's margin here is **real but overstated**
- BM25's 0.636 MRR is **understated**
- the hybrid result is the one to trust least, since it depends on the mix

## Next

1. Add a second gold set of terminology-style questions and evaluate the mix.
   The current set answers "which strategy handles paraphrase best", not
   "which strategy should ship".
2. Re-run on a corpus of a few hundred chunks. BM25's IDF is close to
   meaningless at 20 documents, which is likely most of why fusion hurts.
3. Only then decide whether hybrid+rerank earns its latency.

Recording this because it contradicts the assumption the pipeline was built
on. The architecture supports all four strategies precisely so the choice can
be measured rather than asserted, and right now the measurement does not favour
the most elaborate option.
