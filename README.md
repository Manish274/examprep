# ExamPrep

An exam preparation app. A student uploads their own study material — PDFs and
PowerPoint decks — and the app indexes it, then answers questions, writes
multiple-choice quizzes and builds flashcards **strictly from that material**,
with a citation on every claim.

Grounded accuracy is the design priority. When the uploaded material cannot
support an answer, the app says so rather than filling the gap from the model's
own knowledge.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the decision record.

---

## How it works

- **A visit, not an account.** A student types a name on the start screen and
  gets an empty workspace. There are no passwords and nothing persists between
  visits.
- **Upload, then study.** The `+` in the composer takes a PDF or slide deck.
  It is parsed, chunked, embedded and indexed in the background, with progress
  shown on the file. The text is usable within seconds; the pictures in it are
  read afterwards and added while the student is already studying.
- **Three modes**, switched in the sidebar: **Chat** answers questions from the
  uploads with citations; **Quiz** writes multiple-choice questions from a
  chosen document and grades them; **Flashcards** makes a deck to flip through.
- **Ending the visit deletes everything.** The sidebar's end-session button
  removes the uploaded files, their vectors, every chat, quiz and card, the
  trace records, and the visitor. A visit abandoned without ending is swept
  the same way once its refresh tokens lapse.

---

## Layout

```
apps/
  web/          Next.js + React   — the app: landing page, start screen, workspace
  api/          Node + Hono + Zod — visits, REST, WebSockets, job producer, dev console
  worker/       Node + BullMQ     — document processing and quiz/card generation jobs
packages/
  db/           Drizzle schema, client and migrations, shared by api and worker
  shared/       Zod schemas for jobs and the WebSocket protocol
services/
  rag/          Python + FastAPI  — parsing, retrieval, reranking, generation, evaluation
scripts/
  dev.mjs       starts the whole stack
docs/           evaluation results
storage/
  uploads/      uploaded documents, one folder per visitor
```

The Node side owns the product: visits, ownership, transport, persistence. The
Python side owns the intelligence: anything touching a document, a vector or a
model. Python never learns who anyone is — identifiers arrive as opaque filter
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

```bash
npm run infra:up
```

```bash
npm run db:migrate
```

With `GEMINI_API_KEY` empty everything runs on mock providers, which is enough
to click through the app; answers are only meaningful with a key.

---

## Running

```bash
npm run dev
```

That starts the containers, rebuilds the shared packages, then runs the RAG
service, the API, the worker and the web app together with their output
prefixed by service. Ctrl-C stops all of them, and each part reloads on save.

```
dev    | postgres, redis and qdrant are up
dev    | shared packages built
rag    | INFO:     Uvicorn running on http://127.0.0.1:8000
worker | INFO: worker listening
api    | INFO: api listening

  ▲  ExamPrep at http://localhost:3000
     dev console  http://localhost:3001/console
```

Check the backend is wired:

```bash
curl http://localhost:3001/health/ready
```

All three checks green means Postgres, Redis and the RAG service are reachable.
Before `npm run infra:up` the answer is `degraded` with per-dependency detail,
which is also correct.

---

## Testing

```bash
npm test
```

```bash
cd services/rag && .venv/Scripts/python.exe -m pytest -q
```

Neither needs an API key; the RAG tests run on mock providers. The retrieval
tests use a throwaway collection in the real Qdrant — mocked, they would pass
while named vectors, the IDF modifier or payload filtering were broken.

`services/rag/tests/test_contract.py` reads the TypeScript source and compares
it with the Python models: the explanation modes and retrieval strategies, the
question types in the database enum, and the chunk fields the worker stores.

Parser and chunker tests run against real generated PDF and PPTX files rather
than mocks, because parsing bugs live in the gap between what a library is
documented to return and what it actually returns. Regenerate them with:

```bash
cd services/rag && .venv/Scripts/python.exe tests/fixtures/generate.py
```

Static checks:

```bash
npm run typecheck
```

```bash
npm run lint
```

```bash
cd services/rag && .venv/Scripts/ruff.exe check . && .venv/Scripts/mypy.exe app
```

---

## The dev console

**http://localhost:3001/console** exercises the pipeline stage by stage. It is
served by the API itself, so it speaks exactly the protocol the web app does,
over the same origin. Development only — the route 404s when
`NODE_ENV=production`.

- **Account** — start and end a visit.
- **Documents** — upload, watch progress arrive over the socket, and open a
  document's chunks to see what retrieval has to work with.
- **Retrieval lab** — one query, all four strategies side by side. A chunk
  keeps the same colour in every column, so a passage only one retriever found
  is obvious.
- **Chat** — streaming answers with citations resolved to document, page and
  heading. The message id next to each answer opens its trace.
- **Tests & cards** — generate, sit a paper, submit, read the grading.
- **Traces** — latency and errors by stage, or one operation span by span.

The retrieval lab is the reason this exists. When an answer is wrong the first
question is always whether the right passage was retrieved at all, and a chat
transcript cannot answer that.

---

## Providers

No model runs on the developer machine; every neural component is a hosted,
free-tier API behind an interface. `.env.example` starts each on its mock or
no-op stand-in; set the provider and its key to switch it on.

| Component | Model | Alternatives |
| --- | --- | --- |
| Embeddings | `gemini-embedding-2` @ 3072 dims | mock |
| Generation | `gemini-3.8-flash`, `gemini-3.5-flash-lite` for rewrites | mock |
| Reranking | Jina `jina-reranker-v2` | Gemini listwise, no-op |
| Sparse | BM25 | — |
| Vision | `gemini-3.1-flash-lite`, a model of its own so it has its own daily budget | mock, no-op |

BM25 needs no model at all: term frequency, IDF and stemming, running locally.

Adding a provider means implementing the matching Protocol in
`services/rag/app/core/interfaces.py` and selecting it by name in
`services/rag/app/container.py`; the pipeline itself does not change.

---

## Chat

The conversation runs over a WebSocket at `/ws`. The first frame must be an
auth frame — a token in the URL would land in access logs and referrer
headers:

```json
{ "type": "auth", "token": "<access token>" }
{ "type": "chat:send", "sessionId": "…", "content": "what is smoothing", "mode": "detailed" }
```

The server replies with `chat:start`, `chat:stage` updates while it searches,
a stream of `chat:token` deltas, `chat:sources`, then `chat:done`. Sessions and
history are REST (`/api/chat/sessions`), so a reloaded page can rebuild the
conversation. A chat covers every ready document in the visit.

What keeps answers honest:

- **The model must cite.** Every claim carries an `[S1]`-style marker, and
  after generation each marker is resolved against the sources actually
  supplied. An invented `[S9]` is reported, never rendered.
- **Refusal is a first-class outcome.** When the material cannot support an
  answer the student is told plainly rather than given a fluent answer from
  the model's own knowledge.
- **A refusal against strong evidence gets one more look.** When the reranker
  rates the top passage relevant and the model still refuses, the answer is
  regenerated once, deterministically, under the same grounding rules.
- **Plainly off-topic questions skip the model.** When both embedding
  similarity and reranker relevance are low, the no-material reply comes back
  without spending a call.
- **Questions about the whole document are recognised.** "Summarise what I
  uploaded" names no subject for retrieval to match, so it is answered from an
  even spread of every document instead of a search.
- **Explanation mode changes style, never grounding.** `simple`, `detailed`
  and `exam` share an identical rule block. The web app always asks for
  `detailed`.

### Conversation memory

A follow-up carries the exchange before it, in two places for two reasons:

- **Retrieval** sees a condensed rewrite. *"How does it differ from a
  trigram"* becomes *"how does a bigram differ from a trigram"* — a pronoun is
  not searchable.
- **Generation** sees the turns themselves, so *"explain that more simply"*
  simplifies the answer just given rather than starting a fresh one.

History is not evidence: every claim in a new answer must still come from the
sources retrieved for it, and markers are stripped from prior turns so an old
`[S1]` cannot resolve to an unrelated new passage. History has its own budget
(`CHAT_HISTORY_TURNS`, `CHAT_HISTORY_MAX_TOKENS`), a small fraction of the
context budget, so the conversation can never evict the evidence.

---

## Quizzes and flashcards

Generation runs as a job, since it is several rate-limited model calls;
progress arrives over the WebSocket after a `subscribe:generation` frame.

The web app writes multiple-choice questions only, graded exactly with no
model call. The API also accepts `short_answer` and `true_false` (the dev
console can request them); short answers are judged by the model in one
batched call and earn partial credit, and if the grader is unavailable the
feedback says so rather than marking the answer wrong.

`GET /api/tests/:id` returns the paper **without** the answer key; the answers
arrive with the graded results.

Every generated question and card names the chunk it came from. Anything the
model cannot trace back to a supplied passage is discarded rather than shown —
a question whose answer is not in the material sends a student to revise the
wrong thing.

---

## Reading content out of images

Lecture material routinely puts substance in pictures — a table screenshotted
from a textbook, a formula pasted as an image, a scanned page. None of it
survives text extraction.

`VISION_PROVIDER=gemini` sends those images to a multimodal model, which
transcribes text and tables verbatim and describes real diagrams.

Reading images is the slow part of ingestion and the only part that can run out
of quota, so a document is ingested in two passes. The first indexes the text
and the document is ready; the second reads the images and swaps in the chunks
they change, while the student is already studying. A scanned PDF has no text
to show in the meantime, so its pages are read in the first pass.

The free tier allows a few dozen vision requests a day, so every image has to
earn its call:

- images below `VISION_MIN_PIXELS` are decoration, and identical images are
  read once;
- every reading is cached by image hash (`vision_cache`), so a retried upload
  or a second copy of the same slides costs nothing;
- images go `VISION_BATCH_SIZE` to a request, each answer matched back to its
  image by number, and at most `VISION_MAX_IMAGES` new ones per document;
- the first sign that the day's quota is spent stops the rest. The text stays
  indexed, and the images left over are read on the next attempt.

**Image-derived text is marked as generated, not extracted.** Chunks carry
`source="vision"`, and the citation says it was read from an image. A
mis-transcribed formula must never be indistinguishable from the document's own
words.

---

## Inspecting retrieval

The RAG service's endpoints take the visitor's user id, which the dev console
shows in its header once a visit has started. All four strategies are exposed:

```bash
curl -s -X POST http://localhost:8000/retrieve -H "content-type: application/json" -H "x-internal-token: dev_internal_token_change_me" -d '{"query":"what is 3NF","user_id":"<id>","strategy":"hybrid","top_k":5}'
```

`strategy` is one of `bm25`, `dense`, `hybrid`, `hybrid_rerank`. Every result
carries its per-stage scores and ranks — seeing that a chunk was 1st by BM25 and
30th by dense retrieval explains a result in a way one fused number cannot.

```bash
curl -s http://localhost:8000/retrieve/stats -H "x-internal-token: dev_internal_token_change_me"
```

Answers the first question when retrieval returns nothing: is the corpus empty,
or is the query bad? `GET /health/config` lists every retrieval and generation
setting in force.

Chunk boundaries decide what retrieval can possibly return, so they are worth
looking at directly. With a file under `storage/uploads/`:

```bash
curl -s -X POST http://localhost:8000/ingest/preview -H "content-type: application/json" -H "x-internal-token: dev_internal_token_change_me" -d '{"storage_key":"<key>","filename":"notes.pdf","kind":"pdf","chunker":"structural"}'
```

Swap `structural` for `fixed_window` to see the naive baseline on the same file.

---

## Measuring retrieval

Retrieval quality is measured, not asserted. An evaluation needs indexed
documents, and those only live as long as a visit: start one, upload the
material, run the evaluation with that visit's user id, and end it afterwards.

```bash
cd services/rag && .venv/Scripts/python.exe scripts/evaluate.py --user <id> --gold gold_sets/study-guide.jsonl --save reports/run.json
```

A gold set for the study-guide fixture is written by hand, deliberately in
words the source text mostly does not use:

```bash
cd services/rag && .venv/Scripts/python.exe scripts/build_reference_gold_set.py --chunks chunks.json
```

A synthetic starting set can be generated from indexed chunks with
`POST /eval/gold-set`, but read its numbers only as a comparison between
strategies: questions written *from* a passage reuse its vocabulary and flatter
keyword retrieval.

Chunk ids are derived from `(document_id, sha256(text))`, so re-ingesting the
same file keeps a gold set valid — but anything that changes a chunk's text (a
chunker change, a parser fix, the vision pass) changes its id. The eval
endpoint refuses a gold set whose ids are all absent rather than reporting
`Recall@5 = 0.000` for every strategy.

Differences between strategies come with a **paired bootstrap and a sign
test**, because on a gold set of twenty questions a gap of a few points is not
a result. Saved reports carry per-query metrics, so a past run can be
re-analysed against a different baseline for free:

```bash
cd services/rag && .venv/Scripts/python.exe scripts/evaluate.py --analyse reports/m6-nlp-lecture.json --baseline hybrid_rerank
```

Results so far are in [docs/evaluation-baseline.md](docs/evaluation-baseline.md).
They do **not** favour hybrid with reranking, which is why they are written
down.

---

## Observability

Every stage of a request records what it did: how the question was rewritten,
which chunks came back and in what order, how far the reranker moved them, how
much context was built, and which sources the answer cited. Spans go to the
`traces` table, and to Langfuse as well if it is configured:

```
TRACER=postgres            # or: langfuse, postgres,langfuse, memory, none
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com
```

A trace never fails a request and never slows one down: writes go onto a
bounded queue drained in the background, and failures are counted rather than
raised. The counters are on `/health`, so a sink silently dropping everything
is visible:

```json
"tracing": {"sink": "postgres", "written": 128, "dropped": 0, "failed": 0, "queued": 0}
```

Trace rows hold a visitor's questions and excerpts of their uploads, so they
are deleted with the visitor. While a visit is live they can be read back by
stage, or one operation end to end — the correlation id is the assistant
message id, so "why did it answer that?" is a single lookup:

```bash
curl -s "http://localhost:3001/api/traces?hours=24" -H "authorization: Bearer <token>"
curl -s http://localhost:3001/api/traces/<message-id> -H "authorization: Bearer <token>"
```

```bash
cd services/rag && .venv/Scripts/python.exe scripts/traces.py --correlation <message-id>
```

```
- retrieve  (2036ms, ok)
     output: {"returned": 8, "chunk_ids": [...], "scores": [0.1895, 0.0362, ...]}
- rerank  (1097ms, ok)
     output: {"returned": 8, "top_changed": false, "max_promotion": 12, "mean_abs_shift": 4.75}
- generate  (16123ms, FAILED)
     error: RuntimeError: llm complete failed after 4 attempts: llm rate limited: 429 ...
```

That example is the point: the student saw a failure, and the trace says
retrieval was healthy and found the right passage at rank 1 — only generation
was rate limited.

---

## Notes

- Ingestion is bound by free-tier rate limits, so large documents process
  slowly rather than failing. Embeddings are cached on
  `(model_id, sha256(chunk_text))` and image readings on the image's hash,
  making re-ingests and evaluation re-runs free. The RAG service runs one
  ingest per document at a time, so a worker retry after a timeout waits on
  the run already going rather than starting the work again.
- Gemini's free tier allows about 20 generation requests a day per model;
  switching `LLM_MODEL` buys another 20. Embeddings have a separate, larger
  budget.
- Free-tier providers may use submitted content for product improvement. Worth
  revisiting before any real student data is involved.
- `EMBEDDING_DIMENSIONS` is fixed once a corpus is indexed. The service
  refuses to start against a collection whose width disagrees rather than
  indexing incomparable vectors.
