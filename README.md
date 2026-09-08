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

**Milestone 6 — Tracing and observability. Complete.**

| Milestone | Scope | State |
| --- | --- | --- |
| 0 | Monorepo, infra, schema, service skeletons, test harness | done |
| 1 | Document ingestion, chunking, metadata preservation | done |
| 2 | Embeddings, Qdrant index, BM25, hybrid search, RRF | done |
| 3 | Reranking, context construction, eval harness | done |
| 4 | LLM provider, grounded chat, WebSocket streaming | done |
| 5 | Test generation, grading, flashcards | done |
| 6 | Tracing, observability, evaluation at scale | done |

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

## Testing it by hand

A console for exercising the whole pipeline lives at
**http://localhost:3001/console** once the API is running. It is served by the
API itself rather than hosted separately, which is the point: same origin, so
there is no CORS entry to maintain and the WebSocket upgrades with nothing
loosened to let it. It speaks exactly the protocol the real frontend will.
Development only -- the route 404s when `NODE_ENV=production`.

```bash
npm run dev
```

That brings up the containers, the RAG service, the API and the worker together,
prefixes their output by service, and prints the console URL once it answers.
Ctrl-C stops all of them. Every part reloads on save.

```
dev    | postgres, redis and qdrant are up
rag    | INFO:     Uvicorn running on http://127.0.0.1:8000
worker | INFO: worker listening
api    | INFO: api listening

  ▲  Console live at http://localhost:3001/console
```

Six tabs, one per stage worth inspecting on its own:

- **Documents** -- upload, then watch the stage and percentage arrive over the
  socket. Open a document's chunks to see what retrieval will have to work with.
- **Retrieval lab** -- one query, all four strategies side by side. A chunk
  keeps the same colour in every column, so a passage only one retriever found
  is obvious, and the header reports how many chunks all of them returned. When
  that number equals the total, fusion has nothing to fuse and can only reorder.
- **Chat** -- streaming answers with citations resolved to document, page and
  heading. The message id next to each answer opens its trace.
- **Tests & cards** -- generate, sit a paper, submit, read the grading.
- **Traces** -- latency and errors by stage, or one operation span by span.

The retrieval lab is the reason this exists. When an answer is wrong the first
question is always whether the right passage was retrieved at all, and a chat
transcript cannot answer that.

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

## Tests and flashcards

```bash
curl -X POST http://localhost:3001/api/tests -H "authorization: Bearer <tok>" -H "content-type: application/json" -d '{"documentId":"<id>","questionCount":10,"types":["mcq","short_answer"]}'
```

Returns `202` with a test id; generation runs as a job, since ten questions is
several rate-limited model calls. Progress arrives over the WebSocket after a
`subscribe:generation` frame.

`GET /api/tests/:id` returns the paper **without** the answer key — that would
put the answers in the browser before the test is taken. Start an attempt,
submit, and the answers arrive with the results.

Grading splits by question type. Multiple choice and true/false are compared
directly: no model, no cost, no chance of a wrong verdict. Only short answers
are judged, in one batched call, and they earn **partial credit** — a student
who has the idea but omits a condition has not failed the question. If the
grader is unavailable the answer is not silently marked wrong; the feedback
says it could not be marked.

Every generated question and card names the chunk it came from. Anything the
model cannot trace back to a supplied passage is discarded rather than shown —
a question whose answer is not in the material sends a student to revise the
wrong thing.

---

## Chatting with a document

The conversation runs over a WebSocket at `/ws`. The first frame must be an
auth frame — a token in the URL would land in access logs and referrer
headers:

```json
{ "type": "auth", "token": "<access token>" }
{ "type": "chat:send", "sessionId": "…", "content": "what is smoothing", "mode": "simple" }
```

The server replies with `chat:start`, a stream of `chat:token` deltas,
`chat:sources`, then `chat:done`. Sessions and history are REST
(`/api/chat/sessions`), so a reloaded page can rebuild the conversation.

Three things keep answers honest:

- **The model must cite.** Every claim carries an `[S1]`-style marker, and
  after generation each marker is resolved against the sources actually
  supplied. An invented `[S9]` is reported, never rendered.
- **Refusal is a first-class outcome.** When the material cannot support an
  answer the model returns a refusal token, and the student is told plainly
  rather than given a fluent answer from the model's own knowledge.
- **Explanation mode changes style, never grounding.** `simple`, `detailed`
  and `exam` share an identical rule block; only the style section differs.

### Conversation memory

Every message is persisted to `messages`, with the sources an answer actually
cited in `message_sources`. A follow-up carries the exchange before it, and
that happens in two distinct places for two distinct reasons:

- **Retrieval** sees a condensed rewrite. *"How does it differ from a
  trigram"* becomes *"how does a bigram differ from a trigram"* — a pronoun is
  not searchable, and this is what makes a follow-up retrieve anything at all.
- **Generation** sees the turns themselves. Condensing alone left the
  answering model blind to the conversation: it retrieved the right passages
  and then wrote them up as though nothing had been asked before, so *"explain
  that more simply"* produced a fresh lecture rather than a simpler version of
  the answer just given.

Two rules protect grounding while doing it:

- **History is not evidence.** Prior turns say what the student is referring
  to. Every factual claim in the new answer must still come from the sources
  retrieved for it, and the prompt says so explicitly.
- **Stale markers are stripped.** A previous answer's `[S1]` referred to
  whatever was retrieved *then*. The new question retrieves a different set, so
  a copied marker would resolve to an unrelated passage and show the student a
  confidently wrong source. Markers are removed from prior turns before the
  model sees them.

History has its own budget — `CHAT_HISTORY_TURNS` (8) and
`CHAT_HISTORY_MAX_TOKENS` (1500) — deliberately a small fraction of
`CONTEXT_MAX_TOKENS`. The conversation is an answer's setting; the sources are
its evidence, and the setting must never evict the evidence.

Frames from one socket are handled in order. Nothing awaits one `onMessage`
before the next fires, so a client that sends its auth frame and first question
together would otherwise have the question dispatched mid-verification and
rejected — and two questions in quick succession would generate concurrently,
with the second overwriting the abort controller of the first.

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
randomly, so a retry or a worker rerun keeps a gold set valid. Anything that
changes a chunk's **text** does not: a chunker change, a parser fix, or
enabling the vision pass rewrites the hash and therefore the id. That happened
here, and every id in the NLP gold set went stale at once. Two guards now
exist — the eval endpoint refuses a gold set whose ids are all absent from the
index rather than reporting `Recall@5 = 0.000` for every strategy, and
`scripts/remap_gold_set.py` repoints a stale set by matching each entry's
recorded section heading:

```bash
cd services/rag && .venv/Scripts/python.exe scripts/remap_gold_set.py --user <id> --gold gold_sets/nlp-lecture.jsonl --dry-run
```

Differences between strategies are reported with a **paired bootstrap and a
sign test**, because on a gold set of nineteen questions a gap of a few points
is not a result:

```
metric      strategy        vs                   diff              95% CI       p     W-L-T   sign p
mrr         hybrid          dense              -0.211     [-0.333,-0.088]  0.000*    0-8-11    0.008
```

Saved reports carry per-query metrics, so a past run can be re-analysed against
a different baseline for free:

```bash
cd services/rag && .venv/Scripts/python.exe scripts/evaluate.py --analyse reports/m6-nlp-lecture.json --baseline hybrid_rerank --user x --gold x
```


---

## Observability

Every stage of a request records what it did: how the question was rewritten,
which chunks came back and in what order, how far the reranker moved them, how
much context was built, and which sources the answer actually cited. Spans go
to the `traces` table by default, and to Langfuse as well if it is configured:

```
TRACER=postgres            # or: langfuse, postgres,langfuse, none
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=http://localhost:3000
```

Two rules hold everywhere: a trace never fails a request, and never slows one
down. Writes go onto a bounded queue drained by a background task, and every
failure is counted rather than raised. Those counters are on `/health`, so a
sink that is silently dropping everything is visible:

```json
"tracing": {"sink": "postgres", "written": 128, "dropped": 0, "failed": 0, "queued": 0}
```

Read the table back by stage, or follow one operation end to end:

```bash
cd services/rag && .venv/Scripts/python.exe scripts/traces.py --hours 6
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

That example is the point of the whole thing: the student saw "Chat failed",
and the trace says retrieval was healthy and found the right passage at rank 1
— only generation was rate limited. The two failures need completely different
responses and are indistinguishable from the outside.

The same data is available over the API, scoped to the owner, since a trace
holds which of a student's documents matched and what was in them:

```bash
curl -s http://localhost:3001/api/traces?hours=24 -H "authorization: Bearer <token>"
curl -s http://localhost:3001/api/traces/<message-id> -H "authorization: Bearer <token>"
```

The correlation id is the assistant message id, so "why did it answer that?" is
a single lookup.

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
