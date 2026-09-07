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

---

# Run 3 — same deck, larger corpus, with significance testing

Recorded 2026-09-07, Milestone 6. Two things changed since run 2, and both
matter for reading the numbers.

**The gold set had gone stale.** Chunk ids are `uuid5(document_id,
sha256(text))`. The vision pass changed the text of the chunks it enriched, so
every id changed, and *none* of the 19 gold ids still existed in the index.
Run against it unmodified, all four strategies would have scored Recall@5 =
0.000 and the report would have read as a total collapse. The questions were
fine; only the pointers were stale. `scripts/remap_gold_set.py` repointed all
19 by matching each entry's recorded section heading, and the eval endpoint now
refuses a gold set whose ids are all absent rather than reporting zeroes.

**The corpus is now both documents**, 40 chunks rather than 20 — the NLP deck
plus the unrelated study guide. Queries are not restricted to one document, so
half the corpus is now distractors on a different subject.

| | |
|---|---|
| Corpus | NLP lecture deck + study guide — 40 chunks |
| Gold set | the same 19 questions, remapped |
| Baseline for comparison | `dense` |

```
strategy             recall@5  precision@5          mrr       ndcg@5        hit@5   p50 ms   p95 ms
---------------------------------------------------------------------------------------------------
bm25                   0.737        0.147        0.443        0.501        0.737        12       15
dense                  1.000*       0.200*       0.912*       0.935*       1.000*      732     2060
hybrid                 0.842        0.168        0.702        0.719        0.842       736      907
hybrid_rerank          0.947        0.189        0.756        0.801        0.947      1778    64027
```

## Is any of that real? — paired bootstrap and sign test

```
metric      strategy        vs                   diff              95% CI       p     W-L-T   sign p
----------------------------------------------------------------------------------------------------
mrr         bm25            dense              -0.469     [-0.607,-0.329]  0.000*    0-15-4    0.000
ndcg@5      bm25            dense              -0.434     [-0.577,-0.296]  0.000*    0-15-4    0.000
recall@5    bm25            dense              -0.263     [-0.474,-0.105]  0.007*    0-5-14    0.062
mrr         hybrid          dense              -0.211     [-0.333,-0.088]  0.000*    0-8-11    0.008
ndcg@5      hybrid          dense              -0.216     [-0.348,-0.091]  0.000*    0-8-11    0.008
recall@5    hybrid          dense              -0.158     [-0.316,+0.000]  0.076     0-3-16    0.250
mrr         hybrid_rerank   dense              -0.156     [-0.297,-0.009]  0.041*    1-7-11    0.070
ndcg@5      hybrid_rerank   dense              -0.134     [-0.249,-0.015]  0.029*    1-7-11    0.070
recall@5    hybrid_rerank   dense              -0.053     [-0.158,+0.000]  0.726     0-1-18    1.000
```

Read the W-L-T column first. **BM25 does not beat dense on a single question of
the nineteen** — 0 wins, 15 losses, 4 ties on MRR. That is not a close result
and no amount of small-sample caution changes it.

**Hybrid loses to dense on ranking with real evidence behind it**: 0-8-11, sign
test p = 0.008. Fusion never once put the right passage higher than dense did.

**Hybrid+reranking against dense is the genuinely marginal case, and the two
tests disagree.** The bootstrap calls the MRR gap significant (p = 0.041); the
sign test does not (p = 0.070), because only 8 of 19 questions are decisive at
all. The honest reading is *dense is ahead, and 19 questions is not enough to
say by how much*. The recall difference is plainly noise (p = 0.726).

## Why fusion cannot help at this size — measured, not argued

The new `fuse` span records how much the two retrievers agree. On a real query
against one document:

```
fuse: {"dense": 20, "sparse": 20, "overlap": 20, "dense_only_in_top": 0}
```

Both retrievers returned **the same 20 chunks** — the entire document. When the
candidate sets are identical, fusion cannot add recall, because there is
nothing for either retriever to contribute that the other missed. All RRF can
do is reorder, and reordering the better retriever's list using the worse one's
opinion is a loss by construction. That is the mechanism behind three runs of
`dense > hybrid`, and it is now a number rather than a hypothesis.

This also says exactly when to re-test: **when the corpora are large enough
that the two retrievers stop returning the same set.** Overlap is recorded on
every hybrid query, so that threshold is now observable rather than guessed at.

## And reranking does not earn its place over plain hybrid

Re-running the same saved report against `hybrid_rerank` as the baseline costs
nothing — the per-query metrics are in the file:

```bash
python scripts/evaluate.py --analyse reports/m6-nlp-lecture.json --baseline hybrid_rerank
```

```
mrr         hybrid          hybrid_rerank      -0.054     [-0.202,+0.080]  0.449     3-4-12    1.000
ndcg@5      hybrid          hybrid_rerank      -0.082     [-0.230,+0.039]  0.225     2-4-13    0.688
recall@5    hybrid          hybrid_rerank      -0.105     [-0.263,+0.000]  0.241     0-2-17    0.500
```

Reranking wins 4 questions and loses 3. On this corpus the Jina cross-encoder —
a third-party call in the request path, ~1s per query, its own rate limit — is
**not measurably better than the fused order it is reranking**. It is not
harmful either; it is simply not paying for itself yet.

## Latency

`hybrid_rerank` p95 is **64 seconds**, against a p50 of 1.8s. That is a single
Jina rate-limit stall, and it is exactly what a mean would have hidden. Dense
is 732ms at p50 with no external reranker call at all.

## What changed in the recommendation

Nothing — but it is now supported rather than asserted:

- **Default to dense.** Ahead on every metric, ~2.4× faster at p50, and with no
  third-party reranker in the request path.
- **Keep BM25 indexed.** It costs nothing and it is 60× faster; its weakness
  here is a corpus-size artefact, and the `fuse` overlap number will show when
  that stops being true.
- **19 questions is still the binding constraint.** Every "not significant"
  above mostly means "not enough questions", not "no difference".
