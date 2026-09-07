import { env } from "../env.js";
import { upstreamFailure } from "./errors.js";

/**
 * Thin typed client for the Python RAG service. Every call carries the internal
 * service token and a correlation id so a single student request can be traced
 * across both backends.
 */
export interface RagRequestOptions {
  correlationId?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export async function ragFetch<T>(
  path: string,
  init: RequestInit = {},
  options: RagRequestOptions = {},
): Promise<T> {
  const { RAG_SERVICE_URL, INTERNAL_SERVICE_TOKEN } = env();
  const timeoutMs = options.timeoutMs ?? 30_000;

  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, timeoutMs);
  if (options.signal) {
    options.signal.addEventListener("abort", () => {
      controller.abort();
    });
  }

  try {
    const res = await fetch(new URL(path, RAG_SERVICE_URL), {
      ...init,
      signal: controller.signal,
      headers: {
        "content-type": "application/json",
        "x-internal-token": INTERNAL_SERVICE_TOKEN,
        ...(options.correlationId
          ? { "x-correlation-id": options.correlationId }
          : {}),
        ...init.headers,
      },
    });

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw upstreamFailure(
        `RAG service ${res.status} on ${path}: ${body.slice(0, 500)}`,
      );
    }
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw upstreamFailure(`RAG service timed out after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export interface RagHealth {
  status: "ok" | "degraded";
  qdrant: boolean;
  providers: Record<string, string>;
}

/**
 * Drops a document's vectors from the index.
 *
 * Deleting the Postgres rows without this leaves the vectors behind, and the
 * deleted material keeps answering the student's questions.
 */
export const ragDeleteDocument = (
  documentId: string,
  userId: string,
  options?: RagRequestOptions,
): Promise<{ document_id: string; removed: number }> =>
  ragFetch(
    "/documents/delete",
    {
      method: "POST",
      body: JSON.stringify({ document_id: documentId, user_id: userId }),
    },
    { timeoutMs: 30_000, ...options },
  );

export const ragHealth = (options?: RagRequestOptions): Promise<RagHealth> =>
  ragFetch<RagHealth>("/health", { method: "GET" }, { timeoutMs: 5_000, ...options });

/** One chunk of a streamed chat answer. */
export type ChatStreamEvent =
  | { type: "token"; delta: string }
  | { type: "sources"; sources: ChatSource[] }
  | { type: "done"; unsupported: boolean; retrieved: number; rewrittenQuery: string | null }
  | { type: "error"; message: string };

export interface ChatSource {
  marker: string;
  chunk_id: string;
  document_id: string;
  document_name: string;
  page_number: number | null;
  slide_number: number | null;
  heading_path: string[];
  snippet: string;
}

export interface ChatStreamRequest {
  question: string;
  userId: string;
  mode: "simple" | "detailed" | "exam";
  documentIds?: string[] | null;
  history?: { role: string; content: string }[];
}

/**
 * Consumes the RAG service's SSE stream and yields typed events.
 *
 * Parsing SSE by hand rather than using EventSource because that API cannot
 * issue a POST, and the request body carries the conversation history.
 */
export async function* ragChatStream(
  request: ChatStreamRequest,
  options: { correlationId?: string; signal?: AbortSignal } = {},
): AsyncGenerator<ChatStreamEvent> {
  const { RAG_SERVICE_URL, INTERNAL_SERVICE_TOKEN } = env();

  const response = await fetch(new URL("/chat/stream", RAG_SERVICE_URL), {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
      "x-internal-token": INTERNAL_SERVICE_TOKEN,
      ...(options.correlationId
        ? { "x-correlation-id": options.correlationId }
        : {}),
    },
    body: JSON.stringify({
      question: request.question,
      user_id: request.userId,
      mode: request.mode,
      document_ids: request.documentIds ?? null,
      history: request.history ?? [],
    }),
    ...(options.signal ? { signal: options.signal } : {}),
  });

  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => "");
    throw upstreamFailure(
      `RAG chat failed (${response.status}): ${detail.slice(0, 300)}`,
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Events are separated by a blank line; anything after the last one is
      // a partial event and must stay in the buffer.
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";

      for (const frame of frames) {
        let name = "message";
        const dataLines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) name = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (dataLines.length === 0) continue;

        let payload: Record<string, unknown>;
        try {
          payload = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
        } catch {
          continue;
        }

        if (name === "token") {
          yield { type: "token", delta: String(payload.delta ?? "") };
        } else if (name === "sources") {
          yield { type: "sources", sources: (payload.sources ?? []) as ChatSource[] };
        } else if (name === "done") {
          yield {
            type: "done",
            unsupported: Boolean(payload.unsupported),
            retrieved: Number(payload.retrieved ?? 0),
            rewrittenQuery: (payload.rewritten_query as string | null) ?? null,
          };
        } else if (name === "error") {
          yield { type: "error", message: String(payload.message ?? "unknown") };
        }
      }
    }
  } finally {
    // Releasing the lock matters when the consumer breaks out early, e.g.
    // because the student closed the tab mid-answer.
    reader.releaseLock();
  }
}

export interface GradableAnswerInput {
  question_id: string;
  question_type: string;
  prompt: string;
  correct_answer: string;
  explanation: string;
  response: string | null;
  options: string[] | null;
  source_text: string;
}

export interface GradedAnswer {
  question_id: string;
  is_correct: boolean;
  awarded: number;
  feedback: string;
}

/**
 * Grades a submitted attempt.
 *
 * Runs in the request rather than a job: the student is sitting in front of
 * their results, and only the short answers need a model at all. The timeout
 * is generous but far below the generation ceiling.
 */
export const gradeAnswers = (
  answers: GradableAnswerInput[],
  options: RagRequestOptions = {},
): Promise<{ graded: GradedAnswer[]; score: number; max_score: number }> =>
  ragFetch(
    "/grade",
    { method: "POST", body: JSON.stringify({ answers }) },
    { timeoutMs: 180_000, ...options },
  );

/** One retrieved chunk, as the RAG service reports it. */
export interface RagRetrievedChunk {
  chunk_id: string;
  text: string;
  token_count: number;
  score: number;
  dense_score: number | null;
  sparse_score: number | null;
  rrf_score: number | null;
  rerank_score: number | null;
  dense_rank: number | null;
  sparse_rank: number | null;
  document_id: string;
  document_name: string;
  chunk_index: number;
  page_number: number | null;
  slide_number: number | null;
  heading: string | null;
  heading_path: string[];
  source: string;
}

export interface RagRetrieveResponse {
  query: string;
  strategy: string;
  count: number;
  took_ms: number;
  results: RagRetrievedChunk[];
}

/**
 * Runs one retrieval strategy, scoped to a user.
 *
 * The user id is supplied by the caller from the verified token, never by the
 * browser: it is the whole isolation boundary on the Python side.
 */
export const ragRetrieve = (
  request: {
    query: string;
    userId: string;
    strategy: string;
    documentIds?: string[] | null;
    topK?: number;
  },
  options: RagRequestOptions = {},
): Promise<RagRetrieveResponse> =>
  ragFetch(
    "/retrieve",
    {
      method: "POST",
      body: JSON.stringify({
        query: request.query,
        user_id: request.userId,
        strategy: request.strategy,
        document_ids: request.documentIds ?? null,
        top_k: request.topK ?? 10,
      }),
    },
    { timeoutMs: 120_000, ...options },
  );
