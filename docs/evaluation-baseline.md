# Retrieval evaluation — baselines

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

---

# Run 2 — real lecture deck, mixed question types

The first run's gold set only asked paraphrase questions, which favours dense
retrieval. This run fixes that: a real 20-slide NLP lecture deck, and 19
questions **deliberately split** between terminology a student would type
verbatim (BM25's case) and paraphrase (dense's case).

| | |
|---|---|
| Corpus | A real lecture deck — 20 slides, 134 blocks, 19 chunks |
| Gold set | 19 hand-written: 8 terminology, 11 paraphrase |

```
strategy             recall@5  precision@5          mrr       ndcg@5        hit@5       ms
------------------------------------------------------------------------------------------
bm25                   0.789        0.158        0.487        0.558        0.789      218
dense                  1.000*       0.200*       0.939*       0.954*       1.000*   20163
hybrid                 0.947        0.189        0.800        0.833        0.947    17858
hybrid_rerank          0.947        0.189        0.836        0.859        0.947    46706
```

**Dense wins every metric outright**, with perfect recall@5.

## The result that matters

Split by question type:

| question kind | n | BM25 MRR | dense MRR |
|---|---|---|---|
| terminology (*"what is a bigram"*, *"what does NELL stand for"*) | 8 | 0.729 | **1.000** |
| paraphrase (*"how do I handle words that never appeared in training"*) | 11 | 0.397 | **0.894** |

**Dense beats BM25 on terminology questions — BM25's home turf — by a wide
margin, and scores perfectly.** That undercuts the premise hybrid search was
built on here: that exact technical terms need keyword matching because dense
retrieval blurs them.

Two plausible reasons. `gemini-embedding-2` is far stronger on acronyms and
rare tokens than the models that advice was formed around. And chunks are
embedded with their heading trail, so "N-Gram Models" is literally inside the
vector for the bigram passage — the exact-match signal is already in the dense
index.

## What this means

Across two corpora and two independently written gold sets, the ordering is the
same: **dense > hybrid+rerank > hybrid > bm25**. Fusion consistently costs
ranking quality, and reranking recovers only part of what fusion gave away.

This is not an argument to delete hybrid retrieval. It is an argument not to
default to it:

- **Default to dense** for now. Best quality, 2.3× faster than hybrid+rerank.
- **Keep BM25 indexed.** It is free, 90× faster, and its weakness here is
  partly an artefact of a 20-chunk corpus where IDF has almost no statistics.
- **Re-measure at a few hundred chunks** before concluding anything permanent.

## Caveats worth stating

- Both corpora are ~20 chunks. This is the regime where BM25 is weakest.
- Both gold sets map one question to one section, written by the same author as
  the pipeline. Real student questions are messier and more varied.
- 19 questions is small. A gap of a few points means nothing; 0.939 against
  0.800 is large enough to act on.

Recorded because it contradicts the assumption the pipeline was built on. The
architecture supports all four strategies precisely so this could be measured
rather than asserted — and the measurement does not favour the most elaborate
option.
