import type { Job } from "bullmq";
import type { Redis } from "ioredis";
import { eq } from "drizzle-orm";
import { documentChunks, documents } from "@examprep/db";
import {
  documentProcessingJobSchema,
  type DocumentProcessingJob,
  type JobProgress,
} from "@examprep/shared";
import { db } from "../lib/db.js";
import { logger } from "../lib/logger.js";
import { publishDocumentProgress } from "../lib/progress.js";
import { deleteVectors, ingest, RagPermanentError } from "../lib/rag-client.js";

/** Chunk rows per insert, so a large document stays under the parameter limit. */
const INSERT_BATCH = 200;

/**
 * Runs one document through ingestion.
 *
 * The RAG service parses, chunks, embeds and indexes the file in a single
 * call, and hands the chunks back; the worker owns sequencing, progress and
 * persistence, because Postgres belongs to the Node side.
 *
 * So there are two stages a student can be told about: `parsing`, which is
 * that one call and has no fraction to report, and `indexing`, which is the
 * chunks being stored here and is counted.
 */
export async function processDocument(
  job: Job<DocumentProcessingJob>,
  publisher: Redis,
): Promise<void> {
  const { documentId, userId, storageKey, filename, kind } =
    documentProcessingJobSchema.parse(job.data);
  const log = logger.child({ jobId: job.id, documentId, filename });

  const setStatus = async (
    status: "parsing" | "indexing" | "ready" | "failed",
    fields: Partial<typeof documents.$inferInsert> = {},
  ): Promise<void> => {
    await db()
      .update(documents)
      .set({ status, updatedAt: new Date(), ...fields })
      .where(eq(documents.id, documentId));
  };

  const report = (progress: JobProgress): Promise<void> =>
    publishDocumentProgress(publisher, { documentId, progress });

  log.info("processing started");

  try {
    await setStatus("parsing");
    await report({ stage: "parsing", message: `Reading ${filename}` });

    const result = await ingest(
      { documentId, userId, filename, kind, storageKey },
      { correlationId: job.id },
    );

    log.info(
      {
        pages: result.page_count,
        blocks: result.block_count,
        chunks: result.chunk_count,
        indexed: result.indexed,
        cacheHits: result.cache_hits,
        vision: result.vision,
        parser: result.parser,
        timings: result.timings,
      },
      "parsed, embedded and indexed",
    );

    // The student may have ended their session while this ran, and their
    // upload been deleted. The vectors were written regardless; left there,
    // they would outlive everything that points at them.
    const [stillWanted] = await db()
      .select({ id: documents.id })
      .from(documents)
      .where(eq(documents.id, documentId))
      .limit(1);
    if (!stillWanted) {
      await deleteVectors({ documentId, userId }, { correlationId: job.id });
      log.info("document removed during processing; vectors dropped");
      return;
    }

    await setStatus("indexing", { pageCount: result.page_count });
    await report({ stage: "indexing", percent: 0, message: "Storing chunks" });

    // Replace rather than append: a retried job must not double the chunks.
    await db()
      .delete(documentChunks)
      .where(eq(documentChunks.documentId, documentId));

    for (let i = 0; i < result.chunks.length; i += INSERT_BATCH) {
      const slice = result.chunks.slice(i, i + INSERT_BATCH);
      await db()
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
            slideNumber: chunk.slide_number,
            section: chunk.section,
            heading: chunk.heading,
            headingPath: chunk.heading_path,
            charStart: chunk.char_start,
            charEnd: chunk.char_end,
            source: chunk.source,
          })),
        );
      const stored = Math.min(i + INSERT_BATCH, result.chunks.length);
      await report({
        stage: "indexing",
        percent: Math.round((stored / result.chunks.length) * 100),
      });
    }

    await setStatus("ready", {
      chunkCount: result.chunk_count,
      errorMessage: null,
      processedAt: new Date(),
      processingMeta: {
        parser: result.parser,
        chunker: result.chunker,
        blockCount: result.block_count,
        indexed: result.indexed,
        cacheHits: result.cache_hits,
        vision: result.vision,
        timings: result.timings,
        ...result.metadata,
      },
    });
    await report({ stage: "completed", percent: 100, message: "Ready" });

    log.info({ chunks: result.chunk_count }, "processing complete");
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
