import type { Job } from "bullmq";
import type { Redis } from "ioredis";
import { and, eq, notInArray, sql } from "drizzle-orm";
import type { PgUpdateSetSource } from "drizzle-orm/pg-core";
import { documentChunks, documents } from "@examprep/db";
import {
  documentProcessingJobSchema,
  type DocumentProcessingJob,
  type JobProgress,
} from "@examprep/shared";
import { db } from "../lib/db.js";
import { logger } from "../lib/logger.js";
import { publishDocumentProgress } from "../lib/progress.js";
import { nextFigureStep, scheduleFigureRetry } from "../lib/figure-retries.js";
import {
  deleteVectors,
  ingest,
  ingestProgress,
  RagPermanentError,
  type IngestResponse,
  type RagChunk,
} from "../lib/rag-client.js";

/** Chunk rows per insert, so a large document stays under the parameter limit. */
const INSERT_BATCH = 200;

/** How often the RAG service is asked how far through its images it is. */
const PROGRESS_POLL_MS = 2_000;

type Report = (progress: JobProgress) => Promise<void>;

/**
 * Runs one document through ingestion, in two passes.
 *
 * The first indexes the text, and the document is ready -- searchable,
 * quizzable -- as soon as it ends: seconds for a text PDF. Reading the images
 * in it is the slow part, rate limited on a free tier, so it is the second
 * pass, and the student is already studying while it runs.
 *
 * Images the model could not read -- overloaded, or out of quota -- are not
 * dropped: a delayed follow-up job tries them again, and if they still cannot
 * be read the document says how many are missing.
 *
 * The RAG service parses, chunks, embeds and indexes; the worker owns
 * sequencing, progress and persistence, because Postgres belongs to the Node
 * side.
 */
export async function processDocument(
  job: Job<DocumentProcessingJob>,
  publisher: Redis,
): Promise<void> {
  const data = documentProcessingJobSchema.parse(job.data);
  const { documentId, filename } = data;
  const log = logger.child({ jobId: job.id, documentId, filename });
  const report: Report = (progress) =>
    publishDocumentProgress(publisher, { documentId, progress });

  // Every pass is traced under the document's id, retries included.
  const pass = (figures: "defer" | "read", onImages: (percent: number) => Promise<void>) =>
    withImageProgress(documentId, onImages, () =>
      ingest({ ...data, figures }, { correlationId: documentId }),
    );

  if (data.figuresRetry) {
    await retryFigures(job, data, pass, report, log);
    return;
  }

  log.info("processing started");
  const text = await indexText(job, data, pass, report, log);
  if (!text) return;
  if ((text.vision.pending ?? 0) > 0) {
    // Deferred by the text pass, so never tried: read them now.
    await addFigures(job, data, text.figures_pending, pass, report, log);
  } else if (text.figures_pending > 0) {
    // Tried by the text pass -- a scan's pages -- and not all read. Not asked
    // again straight away: the model that just failed them would be asked.
    await settleFigures(data, text.figures_pending, quotaSpent(text), log);
  }
}

/** Whether a pass stopped because the model's daily allowance ran out. */
function quotaSpent(result: IngestResponse): boolean {
  return (result.vision.skipped_quota ?? 0) > 0;
}

/**
 * Records what a pass left unread, and arranges the next try if one is
 * worth making.
 *
 * figuresPending stays set while a try is scheduled, so the web app shows the
 * figures as still on their way; figuresUnread is set only once no try
 * remains, and is what tells the student pages are missing.
 */
async function settleFigures(
  data: DocumentProcessingJob,
  unread: number,
  spent: boolean,
  log: Log,
  fields: PgUpdateSetSource<typeof documents> = {},
): Promise<void> {
  const step = nextFigureStep(unread, spent, data.figuresRetry ?? 0);

  if (step.kind === "retry") {
    await scheduleFigureRetry(data, step.attempt, step.delayMs);
    log.info(
      { unread, attempt: step.attempt, delayMs: step.delayMs },
      "unread images will be tried again",
    );
  } else if (step.kind === "unread") {
    log.warn({ unread, quotaSpent: spent }, "images left unread");
  }

  await db()
    .update(documents)
    .set({
      ...fields,
      figuresPending: step.kind === "retry" ? unread : null,
      figuresUnread: step.kind === "unread" ? step.count : null,
      updatedAt: new Date(),
    })
    .where(eq(documents.id, data.documentId));
}

/**
 * A delayed follow-up: the document is ready, and only the images an earlier
 * pass could not read are left.
 */
async function retryFigures(
  job: Job<DocumentProcessingJob>,
  data: DocumentProcessingJob,
  pass: Pass,
  report: Report,
  log: Log,
): Promise<void> {
  const [row] = await db()
    .select({ status: documents.status, pending: documents.figuresPending })
    .from(documents)
    .where(eq(documents.id, data.documentId))
    .limit(1);

  // Removed in the meantime -- the visit ended, or the student deleted it --
  // or already settled some other way: nothing is waiting on this try.
  if (!row || row.status !== "ready" || !row.pending) {
    log.info({ retry: data.figuresRetry }, "figure retry no longer needed");
    return;
  }

  log.info({ retry: data.figuresRetry, pending: row.pending }, "retrying unread images");
  await addFigures(job, data, row.pending, pass, report, log);
}

type Pass = (
  figures: "defer" | "read",
  onImages: (percent: number) => Promise<void>,
) => Promise<IngestResponse>;

type Log = typeof logger;

/**
 * The first pass. Returns null when the document was removed while it ran.
 *
 * A scanned document has no text without its pictures, so for one of those
 * this pass reads the pages -- the one time it is slow enough to report a
 * percentage while parsing.
 */
async function indexText(
  job: Job<DocumentProcessingJob>,
  { documentId, userId, filename }: DocumentProcessingJob,
  pass: Pass,
  report: Report,
  log: Log,
): Promise<IngestResponse | null> {
  const setStatus = (
    status: "parsing" | "indexing" | "ready" | "failed",
    fields: Partial<typeof documents.$inferInsert> = {},
  ) =>
    db()
      .update(documents)
      .set({ status, updatedAt: new Date(), ...fields })
      .where(eq(documents.id, documentId));

  try {
    await setStatus("parsing");
    await report({ stage: "parsing", message: `Reading ${filename}` });

    const result = await pass("defer", (percent) =>
      report({ stage: "parsing", percent, message: "Reading page images" }),
    );
    logResult(log, result, "text indexed");

    if (!(await stillWanted(job, documentId, userId, log))) return null;

    await setStatus("indexing", { pageCount: result.page_count });
    await report({ stage: "indexing", percent: 0, message: "Storing chunks" });
    await storeChunks(documentId, userId, result.chunks, (percent) =>
      report({ stage: "indexing", percent }),
    );

    await setStatus("ready", {
      chunkCount: result.chunk_count,
      errorMessage: null,
      processedAt: new Date(),
      processingMeta: processingMeta(result),
      figuresPending: result.figures_pending || null,
    });
    await report({ stage: "completed", percent: 100, message: "Ready" });

    log.info(
      { chunks: result.chunk_count, figuresPending: result.figures_pending },
      "document ready",
    );
    return result;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const permanent = err instanceof RagPermanentError;
    const finalAttempt = job.attemptsMade + 1 >= (job.opts.attempts ?? 1);

    log.error({ err: message, permanent, finalAttempt }, "processing failed");

    // Only mark the document failed once no retry remains, so a transient
    // outage does not show the student a permanent error mid-way.
    if (permanent || finalAttempt) {
      await setStatus("failed", { errorMessage: message.slice(0, 1000) });
      await report({ stage: "failed", message });
    }

    if (permanent) {
      // Signals BullMQ to stop retrying: the bytes will not change.
      job.discard();
    }
    throw err;
  }
}

/**
 * The second pass: reads the images the first left, and swaps in the chunks
 * they change.
 *
 * The document is ready throughout, so a failure here costs only its figures,
 * and only for now: whatever is still unread afterwards is tried again later.
 * It is never turned into a failed document, nor into a BullMQ retry that
 * would redo the text pass.
 */
async function addFigures(
  job: Job<DocumentProcessingJob>,
  data: DocumentProcessingJob,
  pending: number,
  pass: Pass,
  report: Report,
  log: Log,
): Promise<void> {
  const { documentId, userId } = data;
  await report({
    stage: "figures",
    message: `Reading ${pending} image${pending === 1 ? "" : "s"}`,
  });

  try {
    const result = await pass("read", (percent) =>
      report({ stage: "figures", percent }),
    );
    logResult(log, result, "figures read");

    if (!(await stillWanted(job, documentId, userId, log))) return;

    await storeChunks(documentId, userId, result.chunks);
    await settleFigures(data, result.figures_pending, quotaSpent(result), log, {
      chunkCount: result.chunk_count,
      processingMeta: processingMeta(result),
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    log.warn({ err: message }, "figures could not be added");
    // Nothing was read, so everything this pass wanted is still unread.
    await settleFigures(data, pending, false, log, {
      processingMeta: sql`coalesce(${documents.processingMeta}, '{}'::jsonb) || ${JSON.stringify({ figuresError: message.slice(0, 500) })}::jsonb`,
    });
  }

  // Sent either way, so the web app re-reads the row and stops showing the
  // figures as on their way.
  await report({ stage: "completed", percent: 100, message: "Ready" });
}

/**
 * Whether the document still exists after a pass.
 *
 * The student may have ended their session while it ran, and their upload
 * been deleted. The vectors were written regardless; left there, they would
 * outlive everything that points at them.
 */
async function stillWanted(
  job: Job<DocumentProcessingJob>,
  documentId: string,
  userId: string,
  log: Log,
): Promise<boolean> {
  const [row] = await db()
    .select({ id: documents.id })
    .from(documents)
    .where(eq(documents.id, documentId))
    .limit(1);
  if (row) return true;

  await deleteVectors({ documentId, userId }, { correlationId: job.id });
  log.info("document removed during processing; vectors dropped");
  return false;
}

/**
 * Makes the stored chunks match `chunks`, keeping every row that did not
 * change.
 *
 * Updated in place rather than deleted and re-inserted, because the figures
 * pass arrives while the student may already be asking questions, and a
 * citation goes with the chunk row it points at. Chunk ids come from the
 * text, so only the chunks a figure actually changed are removed.
 */
async function storeChunks(
  documentId: string,
  userId: string,
  chunks: RagChunk[],
  onProgress?: (percent: number) => Promise<void>,
): Promise<void> {
  await db().transaction(async (tx) => {
    await tx.delete(documentChunks).where(
      and(
        eq(documentChunks.documentId, documentId),
        notInArray(
          documentChunks.id,
          chunks.map((chunk) => chunk.id),
        ),
      ),
    );
    // A figure landing between two chunks shifts every later position, and
    // (document, position) is unique: the old positions are moved out of the
    // way first, so no row collides with a neighbour's mid-update.
    await tx
      .update(documentChunks)
      .set({ chunkIndex: sql`-1 - ${documentChunks.chunkIndex}` })
      .where(eq(documentChunks.documentId, documentId));

    for (let i = 0; i < chunks.length; i += INSERT_BATCH) {
      const slice = chunks.slice(i, i + INSERT_BATCH);
      await tx
        .insert(documentChunks)
        .values(
          slice.map((chunk) => ({
            id: chunk.id,
            documentId,
            userId,
            chunkIndex: chunk.chunk_index,
            text: chunk.text,
            tokenCount: chunk.token_count,
            contentHash: chunk.content_hash,
            pageNumber: chunk.page_number,
            pageEnd: chunk.page_end,
            slideNumber: chunk.slide_number,
            section: chunk.section,
            heading: chunk.heading,
            headingPath: chunk.heading_path,
            charStart: chunk.char_start,
            charEnd: chunk.char_end,
            source: chunk.source,
          })),
        )
        .onConflictDoUpdate({
          target: documentChunks.id,
          set: {
            chunkIndex: sql`excluded.chunk_index`,
            pageNumber: sql`excluded.page_number`,
            pageEnd: sql`excluded.page_end`,
            slideNumber: sql`excluded.slide_number`,
            section: sql`excluded.section`,
            heading: sql`excluded.heading`,
            headingPath: sql`excluded.heading_path`,
            charStart: sql`excluded.char_start`,
            charEnd: sql`excluded.char_end`,
            source: sql`excluded.source`,
          },
        });
      const stored = Math.min(i + INSERT_BATCH, chunks.length);
      await onProgress?.(Math.round((stored / chunks.length) * 100));
    }
  });
}

/**
 * Runs an ingest call while asking the RAG service, every couple of seconds,
 * how far it is through its images -- the one stage slow enough to need a
 * progress bar, and one a single request cannot report on by itself.
 */
async function withImageProgress<T>(
  documentId: string,
  onPercent: (percent: number) => Promise<void>,
  work: () => Promise<T>,
): Promise<T> {
  let stopped = false;
  let polling = false;
  let last = -1;

  const timer = setInterval(() => {
    if (polling) return;
    polling = true;
    ingestProgress(documentId)
      .then(async ({ done, total }) => {
        const percent = total > 0 ? Math.round((done / total) * 100) : -1;
        // Checked after the await: a poll answering once the call has ended
        // must not report a stage the document has already left.
        if (stopped || percent < 0 || percent === last) return;
        last = percent;
        await onPercent(percent);
      })
      // Progress is a courtesy. The ingest call is what decides the outcome.
      .catch(() => undefined)
      .finally(() => {
        polling = false;
      });
  }, PROGRESS_POLL_MS);

  try {
    return await work();
  } finally {
    stopped = true;
    clearInterval(timer);
  }
}

function processingMeta(result: IngestResponse): Record<string, unknown> {
  return {
    parser: result.parser,
    chunker: result.chunker,
    blockCount: result.block_count,
    indexed: result.indexed,
    cacheHits: result.cache_hits,
    vision: result.vision,
    timings: result.timings,
    ...result.metadata,
  };
}

function logResult(log: Log, result: IngestResponse, message: string): void {
  log.info(
    {
      pages: result.page_count,
      blocks: result.block_count,
      chunks: result.chunk_count,
      indexed: result.indexed,
      cacheHits: result.cache_hits,
      vision: result.vision,
      figuresPending: result.figures_pending,
      parser: result.parser,
      timings: result.timings,
    },
    message,
  );
}
