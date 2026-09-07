# ExamPrep RAG Platform

An AI-powered examination preparation platform. Students upload their own study
material — PDFs and PowerPoint decks — and the system indexes it, then answers
questions, generates practice tests and builds flashcards **strictly from that
material**, with a citation on every claim.

Grounded accuracy is the design priority. When the uploaded material cannot
support an answer, the system says so rather than filling the gap from model
priors.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the decision record.

---

## Status

**Milestone 3 — Reranking and evaluation. Complete.**

| Milestone | Scope | State |
| --- | --- | --- |
| 0 | Monorepo, infra, schema, service skeletons, test harness | done |
| 1 | Document ingestion, chunking, metadata preservation | done |
| 2 | Embeddings, Qdrant index, BM25, hybrid search, RRF | done |
| 3 | Reranking, context construction, eval harness | done |
| 4 | LLM provider, grounded chat, WebSocket streaming | next |
| 5 | Test generation, grading, flashcards | |
| 6 | Tracing, gold-set generation, retrieval metrics | |

---

## Layout

```
apps/
  api/          Node + Hono + Zod — auth, CRUD, REST, WebSockets, job producer
  worker/       Node + BullMQ     — async document processing
packages/
  db/           Drizzle schema and client, shared by api & worker
  shared/       Zod schemas and the WebSocket protocol, shared by api & worker
services/
  rag/          Python + FastAPI  — parsing, retrieval, reranking, generation
storage/
  uploads/      uploaded documents (local filesystem for now)
```

The Node side owns the product: identity, ownership, permissions, transport.
The Python side owns the intelligence: anything touching a document, a vector or
a model. Python never learns about users — identifiers arrive as opaque filter
keys.

---

## Requirements

| Tool | Version | Notes |
| --- | --- | --- |
| Node | >= 22 | uses the built-in `.env` parser |
| Python | >= 3.10 | |
| Docker Desktop | any recent | WSL2 backend |

---

## Setup

```bash
npm install
```

```bash
cp .env.example .env
```

```bash
cd services/rag && python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

Then start the infrastructure (requires Docker):

```bash
npm run infra:up
```

```bash
npm run db:migrate
```

---

## Running

```bash
npm run dev:api
```

```bash
npm run dev:worker
```

```bash
cd services/rag && .venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

Check everything is wired:

```bash
curl http://localhost:3001/health/ready
```

All three checks green means the whole path is wired. Before `npm run infra:up`
the answer is `degraded` with per-dependency detail, which is also correct.

---

## Testing

```bash
npm test
```

```bash
cd services/rag && .venv/Scripts/python.exe -m pytest -q
```

Everything runs with **no API key and no infrastructure**. The RAG service falls
back to mock providers when `GEMINI_API_KEY` is empty, so the pipeline and its
tests are exercisable offline.

`services/rag/tests/test_contract.py` reads the TypeScript schemas and compares
them against the Python models — it fails loudly if the two backends drift on
enums or chunk metadata field names.

Parser and chunker tests run against real generated PDF and PPTX files rather
than mocks, because parsing bugs live in the gap between what a library is
documented to return and what it actually returns. Regenerate them with:

```bash
cd services/rag && .venv/Scripts/python.exe tests/fixtures/generate.py
```

---

## Providers

No model runs on the developer machine; every neural component is a hosted API
behind an interface, and all of them are free-tier.

| Component | Default | Swappable to |
| --- | --- | --- |
| Embeddings | `gemini-embedding-2` @ 3072 dims | mock, Jina, Voyage |
| Generation | `gemini-3.8-flash` | any OpenAI-compatible host, Ollama |
| Reranking | `jina-reranker-v2` | Gemini listwise, no-op baseline |
| Sparse | BM25 | — |
| Vision | `gemini-3.5-flash-lite` | any Gemini multimodal model, no-op |

BM25 needs no model at all: term frequency, IDF and stemming, running locally.

Swapping any of them means implementing the matching Protocol in
`services/rag/app/core/interfaces.py` and registering it under a name — no
pipeline code changes.

---

## Inspecting retrieval

All four strategies are exposed, so what a student's question actually matches
can be read directly rather than inferred from an answer:

```bash
curl -s -X POST http://localhost:8000/retrieve -H "content-type: application/json" -H "x-internal-token: dev_internal_token_change_me" -d '{"query":"what is 3NF","user_id":"<id>","strategy":"hybrid","top_k":5}'
```

`strategy` is one of `bm25`, `dense`, `hybrid`, `hybrid_rerank`. Every result
carries its per-stage scores and ranks — seeing that a chunk was 1st by BM25 and
30th by dense retrieval explains a result in a way one fused number cannot.

```bash
curl -s "http://localhost:8000/retrieve/stats" -H "x-internal-token: dev_internal_token_change_me"
```

Answers the first question when retrieval returns nothing: is the corpus empty,
or is the query bad?

---

## Reading content out of images

Lecture material routinely puts substantive content in pictures — a table
screenshotted from a textbook, a formula pasted as an image, a scanned page.
None of it survives text extraction, so a student asking about it gets nothing
back from a document that plainly contains the answer.

`VISION_PROVIDER=gemini` sends those images to a multimodal model, which
transcribes text and tables verbatim and describes real diagrams. It uses the
key the pipeline already holds, so it adds no new credential.

Two filters keep the quota honest, because every image costs a call:

- images below `VISION_MIN_PIXELS` are decoration — bullets, rules, logos
- identical images are described once, so a logo repeated on 60 slides costs
  one call rather than 60

**Image-derived text is marked as generated, not extracted.** Chunks carry
`source="vision"`, and the citation reads *"… · read from an image"*. A
mis-transcribed formula must never be indistinguishable from the document's own
words — that is the failure mode this whole project is built to avoid.

Scanned PDFs are handled by the same path: a page with no text layer is
rendered and read as an image rather than rejected.

---

## Measuring retrieval

Retrieval quality is measured, not asserted. The harness runs a gold set
through all four strategies and prints them side by side:

```bash
cd services/rag && .venv/Scripts/python.exe scripts/evaluate.py --user <id> --gold gold_sets/study-guide.jsonl
```

```
strategy             recall@5  precision@5          mrr       ndcg@5        hit@5       ms
```

A starting gold set can be generated from indexed chunks:

```bash
curl -s -X POST http://localhost:8000/eval/gold-set -H "content-type: application/json" -H "x-internal-token: dev_internal_token_change_me" -d '{"user_id":"<id>","limit":20,"output_path":"gold_sets/mine.jsonl"}'
```

Read synthetic numbers as a **relative** comparison between strategies on
identical data, never as an absolute quality score: questions written *from* a
passage reuse its vocabulary and flatter keyword retrieval. A hand-written set
is worth far more — see `scripts/build_reference_gold_set.py`.

The first measured baseline is recorded in
[docs/evaluation-baseline.md](docs/evaluation-baseline.md). It does **not**
favour hybrid+reranking, which is why it is written down.

Chunk ids are derived from `(document_id, sha256(text))` rather than generated
randomly, so reprocessing a document — a retry, a chunker change, a worker
rerun — keeps a gold set valid. Re-*uploading* the file creates a new document
id and therefore new chunk ids, so a gold set is tied to one upload.

---

## Inspecting chunk quality

Chunk boundaries decide what retrieval can possibly return, so they are worth
looking at directly rather than inferring from scores. With a file under
`storage/uploads/`:

```bash
curl -s -X POST http://localhost:8000/ingest/preview -H "content-type: application/json" -H "x-internal-token: dev_internal_token_change_me" -d '{"storage_key":"<key>","filename":"notes.pdf","kind":"pdf","chunker":"structural"}'
```

Swap `structural` for `fixed_window` to see the naive baseline on the same file.

---

## Notes

- Ingestion is bound by free-tier rate limits, so large documents process
  slowly rather than failing. Embeddings are cached on
  `(model_id, sha256(chunk_text))`, making re-ingests and eval re-runs free.
- Free-tier providers may use submitted content for product improvement. Worth
  revisiting before any real student data is involved.
- `EMBEDDING_DIMENSIONS` is fixed once a corpus is indexed. Changing it
  invalidates every stored vector; the service refuses to start against a
  collection whose width disagrees rather than indexing incomparable vectors.
- Retrieval tests run against a real Qdrant on a throwaway collection. Mocked,
  they would pass while named vectors, the IDF modifier or payload filtering
  were broken — none of those are our code.
