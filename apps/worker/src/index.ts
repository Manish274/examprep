import "./lib/env-file.js";
import { Worker } from "bullmq";

import {
  QUEUE_DOCUMENT_PROCESSING,
  QUEUE_STUDY_GENERATION,
  type DocumentProcessingJob,
  type StudyGenerationJob,
} from "@examprep/shared";
import { loadEnv } from "./env.js";
import { logger } from "./lib/logger.js";
import { closeDb } from "./lib/db.js";
import { createQueueConnection } from "./lib/connection.js";
import { processDocument } from "./jobs/process-document.js";
import { closeFigureRetries } from "./lib/figure-retries.js";
import { generateStudyMaterial } from "./jobs/generate-study.js";

const env = loadEnv();

// A BullMQ worker blocks on its connection while it waits for jobs, so each
// worker gets its own, and publishing progress gets a third.
const documentConnection = createQueueConnection();
const studyConnection = createQueueConnection();
const publisher = createQueueConnection();

const documentWorker = new Worker<DocumentProcessingJob>(
  QUEUE_DOCUMENT_PROCESSING,
  (job) => processDocument(job, publisher),
  {
    connection: documentConnection,
    concurrency: env.WORKER_CONCURRENCY,
    // Only how many ingestions may start per second, which smooths a burst
    // of uploads. It is not what keeps the system inside the free tier: the
    // provider limits are enforced where the calls are made, by the RAG
    // service's rate limiters and its stop when a daily quota runs out.
    limiter: { max: env.WORKER_CONCURRENCY, duration: 1_000 },
  },
);

const studyWorker = new Worker<StudyGenerationJob>(
  QUEUE_STUDY_GENERATION,
  (job) => generateStudyMaterial(job, publisher),
  {
    connection: studyConnection,
    // One at a time: generation is the heaviest consumer of the LLM quota
    // and running several in parallel simply produces rate limits.
    concurrency: 1,
  },
);

for (const [queue, worker] of [
  [QUEUE_DOCUMENT_PROCESSING, documentWorker],
  [QUEUE_STUDY_GENERATION, studyWorker],
] as const) {
  worker.on("completed", (job) => {
    logger.info({ queue, jobId: job.id }, "job completed");
  });
  worker.on("failed", (job, err) => {
    logger.error(
      { queue, jobId: job?.id, attempts: job?.attemptsMade, err: err.message },
      "job failed",
    );
  });
}

logger.info(
  { concurrency: env.WORKER_CONCURRENCY },
  "worker listening",
);

async function shutdown(signal: string): Promise<void> {
  logger.info({ signal }, "shutting down");
  // Workers first, so no job starts on a connection that is about to close.
  await Promise.allSettled([documentWorker.close(), studyWorker.close()]);
  await Promise.allSettled([
    documentConnection.quit(),
    studyConnection.quit(),
    publisher.quit(),
    closeFigureRetries(),
    closeDb(),
  ]);
  process.exit(0);
}

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    void shutdown(signal);
  });
}
