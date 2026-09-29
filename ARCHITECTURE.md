# ExamPrep — Architecture Decision Record

A local-first exam preparation app. Students upload their own study material
(PDF, PPT, PPTX); the app indexes it and answers, quizzes and drills them
strictly from that material.

**Guiding principle: grounded accuracy over everything else.** Every answer,
quiz question and flashcard must trace back to a retrieved chunk, and the app
must say plainly when the material does not support an answer.

---

## 1. Services

| Service | Stack | Responsibility |
| --- | --- | --- |
| `apps/web` | Next.js 16, React 19, TypeScript | landing page, start screen, study workspace (chat, quiz, flashcards) |
| `apps/api` | Node 22+, TypeScript, Hono, Zod | visits, documents, chats, quizzes, flashcards, REST, browser-facing WebSocket, BullMQ producer, dev console |
| `apps/worker` | Node 22+, TypeScript, BullMQ | document processing and quiz/flashcard generation jobs, progress events |
| `services/rag` | Python 3.10+, FastAPI | parsing, chunking, embeddings, dense + sparse retrieval, fusion, reranking, context construction, generation, grading, evaluation |

The browser talks to `apps/api` only. `apps/api` and `apps/worker` call
`services/rag` over HTTP, and chat answers stream back as Server-Sent Events.

## 2. Infrastructure (local, Docker Compose)

- **PostgreSQL** — application data, the embedding cache, the trace store
- **Qdrant** — dense and sparse vectors in one collection
- **Redis** — BullMQ queues, and pub/sub carrying job progress from the worker
  to the API's WebSocket hub
- **Langfuse** — optional second trace sink, hosted, enabled by configuration

## 3. Decisions

### 3.1 No models run on this machine
All neural components are hosted APIs behind interfaces. A local
implementation could be added against the same interface without
architectural change.

### 3.2 Free tier only
No paid services. Provider selection is constrained to free tiers, and the app
is designed to slow down rather than fail when quota is hit: providers rate
limit themselves and retry with backoff, and embeddings are cached.

### 3.3 Providers

| Component | Model | Alternatives behind the interface |
| --- | --- | --- |
| Embeddings | `gemini-embedding-2` @ 3072 dims | `MockEmbeddingProvider` |
| Generation | `gemini-3.8-flash`; `gemini-3.5-flash-lite` for query rewriting | `MockLLMProvider` |
| Reranking | `JinaReranker` (calibrated relevance) | `GeminiListwiseReranker`, `NoOpReranker` |
| Sparse | BM25 on Qdrant's native sparse vectors | — |
| Vision | `gemini-3.5-flash-lite` | `MockVisionProvider`, `NoOpVisionProvider` |

BM25 needs no neural model: term frequency, IDF and stemming only, on CPU.
Learned sparse retrieval (BGE-M3, SPLADE) is out of scope for the same reason
as 3.1.

### 3.4 Document parsing
PyMuPDF for PDF, python-pptx for PPT/PPTX, selected by document kind. Images
large enough to carry content — and PDF pages with no text layer — go to the
vision provider, and the text it returns is marked `source="vision"` all the
way to the citation, so a model's reading of a picture is never mistaken for
the document's own words.

### 3.5 Visits instead of accounts
Drizzle ORM. No accounts: a student types a name and starts a visit — a user
row and a `login_sessions` row. Every upload, chat, quiz and card belongs to
that visit, and ending it, or letting its refresh tokens lapse, deletes them
along with the stored files, the vectors, the trace rows and the visitor.
Short-lived access JWTs carry the visit id and every query is scoped by it;
refresh tokens are stored hashed and rotated on use; the WebSocket has an
explicit authenticated handshake and checks ownership before relaying progress.

### 3.6 Observability
A `Tracer` interface with a Postgres-backed sink capturing query rewrites,
retrieval scores, fusion ranks, rerank movement, context size and latency.
Langfuse is a second sink behind the same interface. Chosen over self-hosting
Langfuse v3 because its ClickHouse + MinIO stack costs 4–6 GB of RAM on a
16 GB machine.

### 3.7 Evaluation
BM25, dense, hybrid and hybrid + rerank are compared on Recall@K, Precision@K,
MRR, NDCG@K and hit rate, with a paired bootstrap and a sign test for every
difference. Gold sets are either written by hand in a student's words
(`scripts/build_reference_gold_set.py`) or generated from indexed chunks as a
starting point; the synthetic kind flatters keyword retrieval and is read only
as a relative comparison. Results are in `docs/evaluation-baseline.md`.

## 4. Swappable interfaces

Defined as Protocols in `services/rag/app/core/interfaces.py`:
`EmbeddingProvider`, `SparseEncoder`, `Reranker`, `LLMProvider`, `Tracer`,
`Retrieval` and `Corpus`; `VisionProvider` sits with its implementations in
`app/vision/providers.py`. `app/container.py` picks one implementation per
stage from configuration, so a provider can be swapped and measured without
touching pipeline code. Parsers are chosen by document kind and chunkers by
name (`structural`, or the `fixed_window` baseline).

## 5. Preserved chunk metadata

`document_id`, `document_name`, `chunk_index`, `page_number`, `slide_number`,
`section`, `heading`, `heading_path`, `char_start`, `char_end`,
`content_hash`, and `source` (text or vision).

Returned with every answer so the web app can render exact citations.

## 6. Known constraints

- Free-tier rate limits throttle ingestion of large documents; providers back
  off and retry rather than failing.
- Embeddings are cached on `(model_id, sha256(chunk_text))`, so re-ingestion
  and evaluation re-runs cost no API calls.
- Free-tier providers may use submitted content for product improvement.
  Revisit before real students upload material.

## 7. Deferred

Deployment, cloud infrastructure and object storage (uploads are on the local
filesystem behind a storage interface).
