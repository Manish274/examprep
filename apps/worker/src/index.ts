import "./lib/env-file.js";
import { Worker } from "bullmq";

import {
  QUEUE_DOCUMENT_PROCESSING,
  type DocumentProcessingJob,
} from "@examprep/shared";
import { loadEnv } from "./env.js";
import { logger } from "./lib/logger.js";
import { createQueueConnection } from "./lib/connection.js";
import { processDocument } from "./jobs/process-document.js";

const env = loadEnv();
const connection = createQueueConnection();
const publisher = createQueueConnection();

const worker = new Worker<DocumentProcessingJob>(
  QUEUE_DOCUMENT_PROCESSING,
  (job) => processDocument(job, publisher),
  {
    connection,
    concurrency: env.WORKER_CONCURRENCY,
    // Ingestion is bound by provider quotas, not by CPU. Limiting jobs here
    // keeps the whole system under the free-tier ceiling.
    limiter: { max: env.WORKER_CONCURRENCY, duration: 1_000 },
  },
);

worker.on("completed", (job) => {
  logger.info({ jobId: job.id }, "job completed");
});

worker.on("failed", (job, err) => {
  logger.error(
    { jobId: job?.id, attempts: job?.attemptsMade, err: err.message },
    "job failed",
  );
});

logger.info(
  { queue: QUEUE_DOCUMENT_PROCESSING, concurrency: env.WORKER_CONCURRENCY },
  "worker listening",
);

async function shutdown(signal: string): Promise<void> {
  logger.info({ signal }, "shutting down");
  await worker.close();
  await Promise.allSettled([connection.quit(), publisher.quit()]);
  process.exit(0);
}

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    void shutdown(signal);
  });
}
