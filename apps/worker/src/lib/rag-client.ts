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
  /** Images a deferred pass left unread; zero once the document is complete. */
  figures_pending: number;
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

/**
 * Calls the RAG service and returns its JSON: a POST when there is a body, a
 * GET when there is not.
 *
 * A 4xx is a RagPermanentError -- the request or the document is the problem,
 * and retrying wastes attempts while delaying the failure the student needs
 * to see. Anything else is a plain Error, which the job retries.
 */
async function call<T>(
  path: string,
  body: unknown,
  options: { correlationId?: string; timeoutMs: number },
): Promise<T> {
  const { RAG_SERVICE_URL, INTERNAL_SERVICE_TOKEN } = env();

  try {
    const response = await fetch(new URL(path, RAG_SERVICE_URL), {
      method: body === undefined ? "GET" : "POST",
      signal: AbortSignal.timeout(options.timeoutMs),
      headers: {
        "content-type": "application/json",
        "x-internal-token": INTERNAL_SERVICE_TOKEN,
        ...(options.correlationId
          ? { "x-correlation-id": options.correlationId }
          : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
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
    if (err instanceof Error && err.name === "TimeoutError") {
      throw new Error(`${path} timed out after ${options.timeoutMs}ms`);
    }
    throw err;
  }
}

/**
 * How long each pass may take. The text pass is seconds for a text document
 * but reads every page of a scanned one; the figures pass reads up to
 * VISION_MAX_IMAGES images at a free-tier rate.
 *
 * Giving up is cheap either way: the RAG service keeps one run per document,
 * so a retry after a timeout waits on the run still going instead of starting
 * the work again.
 */
const INGEST_TIMEOUT_MS = { defer: 600_000, read: 1_800_000 } as const;

/**
 * Parses, chunks, embeds and indexes one stored document.
 *
 * `figures: "defer"` indexes the text and reports the images it left unread;
 * `figures: "read"` is the full pass that adds them.
 */
export const ingest = (
  request: {
    documentId: string;
    userId: string;
    filename: string;
    kind: "pdf" | "pptx" | "ppt";
    storageKey: string;
    figures: "defer" | "read";
  },
  options: { correlationId?: string } = {},
): Promise<IngestResponse> =>
  call(
    "/ingest",
    {
      document_id: request.documentId,
      user_id: request.userId,
      filename: request.filename,
      kind: request.kind,
      storage_key: request.storageKey,
      figures: request.figures,
    },
    { timeoutMs: INGEST_TIMEOUT_MS[request.figures], ...options },
  );

/** How far the document's running ingest is through its images. */
export const ingestProgress = (
  documentId: string,
): Promise<{ running: boolean; done: number; total: number }> =>
  call(`/documents/${encodeURIComponent(documentId)}/progress`, undefined, {
    timeoutMs: 5_000,
  });

/** Drops a document's vectors from the index. */
export const deleteVectors = (
  request: { documentId: string; userId: string },
  options: { correlationId?: string } = {},
): Promise<{ document_id: string; removed: number }> =>
  call(
    "/documents/delete",
    { document_id: request.documentId, user_id: request.userId },
    { timeoutMs: 30_000, ...options },
  );

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

/** Generation is several rate-limited model calls, so the ceiling is high. */
const GENERATION_TIMEOUT_MS = 900_000;

export const generateTest = (
  request: {
    userId: string;
    documentIds: string[];
    questionCount: number;
    types?: string[] | undefined;
    difficulty?: string | undefined;
  },
  options: { correlationId?: string } = {},
): Promise<{ questions: GeneratedQuestion[]; stats: Record<string, number> }> =>
  call(
    "/generate/test",
    {
      user_id: request.userId,
      document_ids: request.documentIds,
      question_count: request.questionCount,
      ...(request.types ? { types: request.types } : {}),
      ...(request.difficulty ? { difficulty: request.difficulty } : {}),
    },
    { timeoutMs: GENERATION_TIMEOUT_MS, ...options },
  );

export const generateFlashcards = (
  request: { userId: string; documentIds: string[]; cardCount: number },
  options: { correlationId?: string } = {},
): Promise<{ cards: GeneratedCard[]; stats: Record<string, number> }> =>
  call(
    "/generate/flashcards",
    {
      user_id: request.userId,
      document_ids: request.documentIds,
      card_count: request.cardCount,
    },
    { timeoutMs: GENERATION_TIMEOUT_MS, ...options },
  );
