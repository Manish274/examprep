import type { Job } from "bullmq";
import type { Redis } from "ioredis";
import type { DocumentProcessingJob } from "@examprep/shared";
import { logger } from "../lib/logger.js";
import { publishProgress } from "../lib/progress.js";

/**
 * Orchestrates one document through the ingestion pipeline.
 *
 * The worker owns sequencing, progress reporting and retries; the Python RAG
 * service owns parsing, chunking, embedding and indexing. Milestone 1 fills in
 * the RAG calls — for now this establishes the contract and the progress flow.
 */
export async function processDocument(
  job: Job<DocumentProcessingJob>,
  redis: Redis,
): Promise<void> {
  const { documentId, filename } = job.data;
  const log = logger.child({ jobId: job.id, documentId, filename });

  log.info("processing started");

  await publishProgress(redis, documentId, {
    stage: "parsing",
    percent: 0,
    message: `Preparing ${filename}`,
  });

  // Milestone 1: POST to the RAG service and stream stage progress back.
  throw new Error(
    "Document processing pipeline is not implemented yet (Milestone 1)",
  );
}
