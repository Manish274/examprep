# ExamPrep

A study app built on retrieval-augmented generation. A student uploads their own
notes — PDFs and PowerPoint decks — and ExamPrep answers questions, writes
quizzes and builds flashcards **strictly from that material**, with a citation
on every claim. When the notes cannot support an answer, it says so instead of
filling the gap from the model's own knowledge.

## What it does

- **A visit, not an account.** Type a name and you get an empty workspace. No
  passwords; ending the visit deletes everything — files, vectors, chats,
  quizzes, cards and traces.
- **Chat** answers questions from your uploads, citing the document and page
  behind every claim.
- **Quiz** writes multiple-choice questions spread across a document, grades
  them, and explains each answer from the source.
- **Flashcards** builds a deck to flip through, each card traced to a passage.

## How it works

```
upload ─▶ parse ─▶ read images ─▶ chunk ─▶ embed + BM25 ─▶ Qdrant
question ─▶ rewrite ─▶ dense + BM25 ─▶ RRF fusion ─▶ rerank ─▶ cited answer
```

**Ingestion.** PyMuPDF and python-pptx extract text, tables and headings (from
font size and weight). Pictures, and scanned pages with no text layer, go to a
vision model that transcribes text and describes diagrams; that text is marked
as *read from an image* all the way to the citation. Blocks are packed into
~512-token chunks along heading boundaries, embedded with Gemini and indexed in
Qdrant beside a BM25 sparse vector. Text is searchable within seconds; images
are read in a second pass while the student works, and any the model could not
read are retried rather than dropped.

**Answering.** A follow-up is rewritten into a standalone question only when it
needs one. Dense and BM25 search run together, are fused with Reciprocal Rank
Fusion, and the top 25 are reranked by a Jina cross-encoder. The best eight go
to the model as numbered sources (`[S1]`, `[S2]`…), with rules to cite every
claim and to reply `NOT_SUPPORTED` when the sources do not cover the question.
Every citation is checked against the sources actually supplied; a plainly
off-topic question is refused without calling the model at all.

**Quizzes and cards.** Generated from an even, varied sample of the whole
document, preferring passages earlier sets did not use. Every item names the
chunk it came from, and anything that cannot be traced back is discarded.
Multiple choice is graded without a model; the answer key never reaches the
browser before submission.

**Built for free tiers.** Every model is a hosted free-tier API — nothing runs
locally. Embeddings and image readings are cached by content hash, images are
read four to a request, each job has its own model and daily quota, and a spent
quota stops work at once instead of retrying for minutes.

**Measured, not assumed.** An evaluation harness compares BM25, dense, hybrid
and hybrid+rerank with Recall@K, MRR and nDCG, plus a paired bootstrap and a
sign test. So far, on small corpora, dense retrieval has led every run — which
is why the comparison is kept and re-run, not settled by default.

## Tech stack

| Layer | Technology |
| --- | --- |
| Web app | Next.js 16, React 19 |
| API | Node, Hono, Zod, WebSockets |
| Jobs | BullMQ on Redis |
| Database | PostgreSQL with Drizzle ORM |
| RAG service | Python, FastAPI, PyMuPDF, python-pptx |
| Vector store | Qdrant (dense + sparse vectors) |
| Models | Gemini (embeddings, answers, vision), Jina reranker v2 |
| Observability | Per-stage traces in Postgres, optional Langfuse |

```
apps/web         Next.js app: landing page, start screen, study workspace
apps/api         visits, REST, WebSockets, job producer, dev console
apps/worker      document processing and quiz/flashcard generation jobs
packages/db      Drizzle schema and migrations, shared by api and worker
packages/shared  Zod schemas for jobs and the WebSocket protocol
services/rag     parsing, retrieval, reranking, generation, evaluation
```

The Node side owns the product — visits, ownership, transport, persistence.
The Python side owns everything that touches a document, a vector or a model,
and never learns who anyone is.

---

## Getting started

### Requirements

| Tool | Version |
| --- | --- |
| Node | 22 or later |
| Python | 3.10 or later |
| Docker Desktop | any recent version |

### Setup

```bash
npm install
```

```bash
cp .env.example .env
```

```bash
cd services/rag && python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

On macOS or Linux use `.venv/bin/python` in place of `.venv/Scripts/python.exe`.

```bash
npm run infra:up
```

```bash
npm run db:migrate
```

`infra:up` starts Postgres, Redis and Qdrant in Docker; `db:migrate` creates the
tables.

### Run

```bash
npm run dev
```

This starts the containers, builds the shared packages, and runs the RAG
service, the API, the worker and the web app together, with output prefixed by
service. Ctrl-C stops everything.

| URL | What |
| --- | --- |
| http://localhost:3000 | the app |
| http://localhost:3001/console | dev console: retrieval lab, traces, chunk viewer |
| http://localhost:3001/health/ready | checks Postgres, Redis and the RAG service |

### Configuration

Out of the box every model runs on a mock, which is enough to click through the
app. For real answers, set these in `.env`:

| Setting | Value |
| --- | --- |
| `GEMINI_API_KEY` | your Gemini API key |
| `EMBEDDING_PROVIDER`, `LLM_PROVIDER`, `VISION_PROVIDER` | `gemini` |
| `JINA_API_KEY` | your Jina API key |
| `RERANKER_PROVIDER` | `jina` |

Models are set by `EMBEDDING_MODEL`, `LLM_MODEL`, `LLM_UTILITY_MODEL` and
`VISION_MODEL`; chunking, retrieval and chat limits are all in `.env.example`.
Gemini's free tier allows roughly 20 generation requests a day per model, and
switching `LLM_MODEL` buys another 20. `EMBEDDING_DIMENSIONS` is fixed once a
collection is indexed.

### Tests and checks

```bash
npm test
```

```bash
cd services/rag && .venv/Scripts/python.exe -m pytest -q
```

```bash
npm run typecheck && npm run lint
```

```bash
cd services/rag && .venv/Scripts/ruff.exe check . && .venv/Scripts/mypy.exe app
```

No API key is needed: the tests run on mock providers, and retrieval tests use
a throwaway collection in the real Qdrant.

### Evaluating retrieval

Start a visit in the dev console and upload
`services/rag/tests/fixtures/study-guide.pdf`. Save its chunks
(`GET /api/documents/<id>/chunks`) as `chunks.json`, build the hand-written
gold set from them, then run the comparison with the user id shown in the
console header:

```bash
cd services/rag && .venv/Scripts/python.exe scripts/build_reference_gold_set.py --chunks chunks.json
```

```bash
cd services/rag && .venv/Scripts/python.exe scripts/evaluate.py --user <id> --gold gold_sets/study-guide.jsonl --save reports/run.json
```

Each answer's trace — rewrite, retrieved chunks, rerank moves, citations — is
in the dev console's Traces tab, or under `GET /api/traces/<message-id>`.
