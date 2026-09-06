# ExamPrep RAG Platform — Architecture Decision Record

Local-first, AI-powered exam preparation platform. Students upload their own
study material (PDF/PPT/PPTX); the system indexes it and answers, quizzes and
drills them strictly from that material.

**Guiding principle: grounded accuracy over everything else.** Every answer,
test question and flashcard must trace back to a retrieved chunk, and the
system must say plainly when the material does not support an answer.

---

## 1. Services

| Service | Stack | Responsibility |
| --- | --- | --- |
| `apps/api` | Node 24, TypeScript, Hono, Zod | auth, users, documents, chat sessions/messages, tests, attempts, flashcards, REST, browser-facing WebSockets, BullMQ producer |
| `apps/worker` | Node 24, TypeScript, BullMQ | async document-processing jobs, progress events, rate limiting against provider quotas |
| `services/rag` | Python 3.10, FastAPI | parsing, chunking, embeddings, dense + sparse retrieval, hybrid fusion, reranking, context construction, LLM calls, evaluation |

The browser talks to `apps/api` only. `apps/api` and `apps/worker` talk to
`services/rag` internally over HTTP + SSE.

## 2. Infrastructure (local, Docker Compose — user installs Docker later)

- **PostgreSQL** — application data + embedding cache + trace store
- **Qdrant** — dense and sparse vectors in a single collection
- **Redis** — BullMQ queues, caching
- **Langfuse** — deferred; added later as a `Tracer` adapter

## 3. Decisions

### 3.1 No models run on this machine
The user does not want local model inference. All neural components are hosted
APIs behind interfaces. This overrides the original spec's "open-source model
running locally" requirement; a local implementation can be added later against
the same interface without architectural change.

### 3.2 Free-tier only
No paid services. Provider selection is constrained to free tiers, and the
system is designed to degrade in speed rather than fail when quota is hit.

### 3.3 Providers

| Component | Default | Alternates behind the interface |
| --- | --- | --- |
| Embeddings | `gemini-embedding-001` | `MockEmbeddingProvider`, Jina, HF Inference |
| LLM | Gemini (key supplied later) | `MockLLMProvider`, any OpenAI-compatible host |
| Reranker | `GeminiListwiseReranker` (RankGPT-style) | `JinaReranker`, `NoOpReranker` |
| Sparse | BM25 (Qdrant native sparse vectors) | — |

BM25 requires no neural model: term frequency, IDF and stemming only. It runs
locally on CPU and is unaffected by the no-local-models constraint. Learned
sparse retrieval (BGE-M3 / SPLADE) is out of scope for the same reason.

### 3.4 Document parsing
Hybrid: PyMuPDF for PDF, python-pptx for PPT/PPTX. Docling is kept as a second
`DocumentParser` implementation for scanned or table-heavy PDFs, compared via
the eval harness rather than assumed better.

### 3.5 Node data layer and auth
Drizzle ORM. Email/password with argon2, short-lived access JWT plus refresh
tokens persisted in Postgres, and an explicit authenticated WebSocket handshake.

### 3.6 Observability
A `Tracer` abstraction with a local Postgres-backed implementation capturing
retrieval scores, fusion ranks, rerank deltas, token counts and latency.
Langfuse becomes one adapter, enabled when the user wants the UI. Chosen over
self-hosting Langfuse v3 now because its ClickHouse + MinIO stack costs 4-6 GB
RAM on a 16 GB machine.

### 3.7 Evaluation
Gold dataset built by generating questions answerable only from a single chunk,
labelling that chunk as ground truth, then filtering through a manual review
CLI. Compares BM25 / dense / hybrid / hybrid+rerank on Recall@K, MRR,
Precision@K and NDCG@K.

## 4. Swappable interfaces

`DocumentParser`, `EmbeddingProvider`, `DenseRetriever`, `SparseRetriever`,
`Reranker`, `LLMProvider`, `StorageProvider`, `Tracer`.

Each is a protocol with a registry, so retrieval strategies and models can be
swapped and compared without touching pipeline code.

## 5. Preserved chunk metadata

`document_id`, `document_name`, `page_number`, `slide_number`, `section`,
`heading_path`, `chunk_index`, `char_span`, `content_hash`.

Returned with every answer so the frontend can render exact citations.

## 6. Known constraints

- Free-tier rate limits throttle ingestion of large documents; the worker rate
  limits and backs off rather than failing.
- Embeddings are cached on `(model_id, sha256(chunk_text))` so re-ingestion and
  eval re-runs cost no API calls.
- Free-tier providers may use submitted content for product improvement. Revisit
  before real students upload material.

## 7. Deferred

Deployment, cloud infrastructure, object storage (local filesystem for now),
Ollama, and the Next.js frontend.
