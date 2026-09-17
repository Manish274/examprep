import { env } from "../env.js";

/** One chunk as returned by the RAG service. Flattened for direct insert. */
export interface RagChunk {
  id: string;
  text: string;
  token_count: number;
  chunk_index: number;
  page_number: number | null;
  slide_number: number | null;
  section: string | null;
  heading: string | null;
  heading_path: string[];
  char_start: number | null;
  char_end: number | null;
  content_hash: string;
  /** "text" or "vision" — vision content is model-generated. */
  source: "text" | "vision";
}

export interface IngestResponse {
  document_id: string;
  /** Chunks written to the vector store. */
  indexed: number;
  /** Embeddings served from cache rather than the provider. */
  cache_hits: number;
  /** Counts from the image-reading pass. */
  vision: Record<string, number>;
  page_count: number;
  block_count: number;
  chunk_count: number;
  parser: string;
  chunker: string;
  timings: Record<string, number>;
  metadata: Record<string, unknown>;
  chunks: RagChunk[];
}

/**
 * Distinguishes a document this pipeline cannot process from a service that is
 * merely unavailable. The first must not be retried -- the bytes will not
 * change -- while the second should be.
 */
export class RagPermanentError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "RagPermanentError";
  }
}

/**
 * Turns a RAG service error into something worth showing a student.
 *
 * FastAPI wraps messages in {"detail": "..."}, and this message ends up on the
 * document row as the explanation for why their upload failed. Left raw, a
 * genuinely useful line like "this looks like a scanned document" arrives
 * buried in a JSON envelope behind a status code.
 */
export function describeFailure(status: number, body: string): string {
  try {
    const parsed: unknown = JSON.parse(body);
    if (
      parsed !== null &&
      typeof parsed === "object" &&
      "detail" in parsed &&
      typeof (parsed as { detail: unknown }).detail === "string"
    ) {
      return (parsed as { detail: string }).detail;
    }
  } catch {
    // Not JSON — fall through to the raw body.
  }
  return `Document processing failed (${status}): ${body.slice(0, 300)}`;
}

export interface IngestRequest {
  documentId: string;
  userId: string;
  filename: string;
  kind: "pdf" | "pptx" | "ppt";
  storageKey: string;
  chunker?: string;
}

export async function ingest(
  request: IngestRequest,
  options: { correlationId?: string; timeoutMs?: number } = {},
): Promise<IngestResponse> {
  const { RAG_SERVICE_URL, INTERNAL_SERVICE_TOKEN } = env();
  // Parsing a large deck is slow, and this call is already inside a background
  // job, so the timeout is generous.
  const timeoutMs = options.timeoutMs ?? 300_000;

  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, timeoutMs);

  try {
    const response = await fetch(new URL("/ingest", RAG_SERVICE_URL), {
      method: "POST",
      signal: controller.signal,
      headers: {
        "content-type": "application/json",
        "x-internal-token": INTERNAL_SERVICE_TOKEN,
        ...(options.correlationId
          ? { "x-correlation-id": options.correlationId }
          : {}),
      },
      body: JSON.stringify({
        document_id: request.documentId,
        user_id: request.userId,
        filename: request.filename,
        kind: request.kind,
        storage_key: request.storageKey,
        ...(request.chunker ? { chunker: request.chunker } : {}),
      }),
    });

    if (!response.ok) {
      const body = await response.text().catch(() => "");
      const message = describeFailure(response.status, body);

      // 4xx means the request or the document is the problem; retrying wastes
      // three attempts and delays the failure the student needs to see.
      if (response.status >= 400 && response.status < 500) {
        throw new RagPermanentError(message, response.status);
      }
      throw new Error(message);
    }

    return (await response.json()) as IngestResponse;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error(`RAG ingest timed out after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

// ── study generation ────────────────────────────────────────

export interface GeneratedQuestion {
  type: "mcq" | "short_answer" | "true_false";
  prompt: string;
  options: string[] | null;
  correct_answer: string;
  explanation: string;
  source_chunk_id: string;
}

export interface GeneratedCard {
  front: string;
  back: string;
  source_chunk_id: string;
}

async function post<T>(
  path: string,
  body: unknown,
  options: { correlationId?: string; timeoutMs?: number } = {},
): Promise<T> {
  const { RAG_SERVICE_URL, INTERNAL_SERVICE_TOKEN } = env();
  // Generation is several rate-limited model calls, so the ceiling is high.
  const timeoutMs = options.timeoutMs ?? 900_000;

  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, timeoutMs);

  try {
    const response = await fetch(new URL(path, RAG_SERVICE_URL), {
      method: "POST",
      signal: controller.signal,
      headers: {
        "content-type": "application/json",
        "x-internal-token": INTERNAL_SERVICE_TOKEN,
        ...(options.correlationId
          ? { "x-correlation-id": options.correlationId }
          : {}),
      },
      body: JSON.stringify(body),
    });

    if (!response.ok) {
      const text = await response.text().catch(() => "");
      const message = describeFailure(response.status, text);
      if (response.status >= 400 && response.status < 500) {
        throw new RagPermanentError(message, response.status);
      }
      throw new Error(message);
    }

    return (await response.json()) as T;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error(`${path} timed out after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/** Drops a document's vectors from the index. */
export const deleteVectors = (
  request: { documentId: string; userId: string },
  options?: { correlationId?: string },
): Promise<{ document_id: string; removed: number }> =>
  post(
    "/documents/delete",
    { document_id: request.documentId, user_id: request.userId },
    { timeoutMs: 30_000, ...options },
  );

export const generateTest = (
  request: {
    userId: string;
    documentIds: string[];
    questionCount: number;
    types?: string[] | undefined;
    difficulty?: string | undefined;
  },
  options?: { correlationId?: string },
): Promise<{ questions: GeneratedQuestion[]; stats: Record<string, number> }> =>
  post(
    "/generate/test",
    {
      user_id: request.userId,
      document_ids: request.documentIds,
      question_count: request.questionCount,
      ...(request.types ? { types: request.types } : {}),
      ...(request.difficulty ? { difficulty: request.difficulty } : {}),
    },
    options,
  );

export const generateFlashcards = (
  request: { userId: string; documentIds: string[]; cardCount: number },
  options?: { correlationId?: string },
): Promise<{ cards: GeneratedCard[]; stats: Record<string, number> }> =>
  post(
    "/generate/flashcards",
    {
      user_id: request.userId,
      document_ids: request.documentIds,
      card_count: request.cardCount,
    },
    options,
  );

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

/** Grading blocks the student, so its ceiling is far lower than generation. */
export const gradeAnswers = (
  answers: GradableAnswerInput[],
  options?: { correlationId?: string },
): Promise<{ graded: GradedAnswer[]; score: number; max_score: number }> =>
  post("/grade", { answers }, { ...options, timeoutMs: 180_000 });
