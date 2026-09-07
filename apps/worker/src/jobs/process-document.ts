import type { Job } from "bullmq";
import type { Redis } from "ioredis";
import { eq } from "drizzle-orm";
import { documentChunks, documents } from "@examprep/db";
import type { DocumentProcessingJob } from "@examprep/shared";
import { db } from "../lib/db.js";
import { logger } from "../lib/logger.js";
import { overallPercent, publishProgress } from "../lib/progress.js";
import { ingest, RagPermanentError } from "../lib/rag-client.js";

/**
 * Runs one document through ingestion.
 *
 * The worker owns sequencing, progress and persistence; the RAG service owns
 * parsing and chunking. Chunks come back over HTTP and are written here,
 * because Postgres belongs to the Node side.
 *
 * Milestone 2 adds embedding and Qdrant indexing between chunking and ready.
 */
export async function processDocument(
  job: Job<DocumentProcessingJob>,
  publisher: Redis,
): Promise<void> {
  const { documentId, userId, storageKey, filename, kind } = job.data;
  const log = logger.child({ jobId: job.id, documentId, filename });

  const setStatus = async (
    status: "parsing" | "chunking" | "indexing" | "ready" | "failed",
    fields: Record<string, unknown> = {},
  ): Promise<void> => {
    await db()
      .update(documents)
      .set({ status, updatedAt: new Date(), ...fields })
      .where(eq(documents.id, documentId));
  };

  const report = async (
    stage: "parsing" | "chunking" | "indexing" | "completed" | "failed",
    current: number,
    total: number,
    message?: string,
  ): Promise<void> => {
    const percent = overallPercent(stage, current, total);
    await publishProgress(publisher, documentId, {
      stage,
      percent,
      ...(message ? { message } : {}),
      current,
      total,
    });
    await job.updateProgress(percent);
  };

  log.info("processing started");

  try {
    await setStatus("parsing");
    await report("parsing", 0, 1, `Reading ${filename}`);

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
        parser: result.parser,
        timings: result.timings,
      },
      "parsed, embedded and indexed",
    );

    await setStatus("chunking", { pageCount: result.page_count });
    await report("chunking", 1, 1, `${result.chunk_count} chunks`);

    await setStatus("indexing");
    await report("indexing", 0, result.chunk_count, "Storing chunks");

    // Replace rather than append: a retried job must not double the chunks.
    await db()
      .delete(documentChunks)
      .where(eq(documentChunks.documentId, documentId));

    if (result.chunks.length > 0) {
      // Chunked inserts keep a large document from exceeding the parameter
      // limit of a single statement.
      const BATCH = 200;
      for (let i = 0; i < result.chunks.length; i += BATCH) {
        const slice = result.chunks.slice(i, i + BATCH);
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
            })),
          );
        await report(
          "indexing",
          Math.min(i + BATCH, result.chunks.length),
          result.chunks.length,
        );
      }
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
        timings: result.timings,
        ...result.metadata,
      },
    });
    await report("completed", 1, 1, "Ready");

    log.info({ chunks: result.chunk_count }, "processing complete");
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const permanent = err instanceof RagPermanentError;
    const finalAttempt = (job.attemptsMade ?? 0) + 1 >= (job.opts.attempts ?? 1);

    log.error({ err: message, permanent, finalAttempt }, "processing failed");

    // Only mark the document failed once no retry remains, so a transient
    // outage does not show the student a permanent error mid-way.
    if (permanent || finalAttempt) {
      await setStatus("failed", { errorMessage: message.slice(0, 1000) });
      await publishProgress(publisher, documentId, {
        stage: "failed",
        percent: 0,
        message,
      });
    }

    if (permanent) {
      // Signals BullMQ to stop retrying: the bytes will not change.
      job.discard();
    }
    throw err;
  }
}
