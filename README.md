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

**Milestone 0 — Foundation. Complete.**

| Milestone | Scope | State |
| --- | --- | --- |
| 0 | Monorepo, infra, schema, service skeletons, test harness | done |
| 1 | Document ingestion, chunking, metadata preservation | next |
| 2 | Embeddings, Qdrant index, BM25, hybrid search, RRF | |
| 3 | Reranking, context construction, eval harness | |
| 4 | LLM provider, grounded chat, WebSocket streaming | |
| 5 | Test generation, grading, flashcards | |
| 6 | Tracing, gold-set generation, retrieval metrics | |

---

## Layout

```
apps/
  api/          Node + Hono + Zod — auth, CRUD, REST, WebSockets, job producer
  worker/       Node + BullMQ     — async document processing
packages/
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
| Docker Desktop | any recent | **not yet installed** — needed for Milestone 1 onward |

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

`degraded` with per-dependency detail is the expected answer before Docker is
running. `rag: ok` confirms the Node → Python link.

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

---

## Providers

No model runs on the developer machine; every neural component is a hosted API
behind an interface, and all of them are free-tier.

| Component | Default | Swappable to |
| --- | --- | --- |
| Embeddings | `gemini-embedding-001` @ 768 dims | mock, Jina, HF Inference |
| Generation | `gemini-2.5-flash` | mock, any OpenAI-compatible host, Ollama |
| Reranking | Gemini listwise (RankGPT-style) | Jina reranker, no-op baseline |
| Sparse | BM25 | — |

BM25 needs no model at all: term frequency, IDF and stemming, running locally.

Swapping any of them means implementing the matching Protocol in
`services/rag/app/core/interfaces.py` and registering it under a name — no
pipeline code changes.

---

## Notes

- Ingestion is bound by free-tier rate limits, so large documents process
  slowly rather than failing. Embeddings are cached on
  `(model_id, sha256(chunk_text))`, making re-ingests and eval re-runs free.
- Free-tier providers may use submitted content for product improvement. Worth
  revisiting before any real student data is involved.
