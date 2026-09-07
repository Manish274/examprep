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
import { createQueueConnection } from "./lib/connection.js";
import { processDocument } from "./jobs/process-document.js";
import { generateStudyMaterial } from "./jobs/generate-study.js";

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

const studyWorker = new Worker<StudyGenerationJob>(
  QUEUE_STUDY_GENERATION,
  (job) => generateStudyMaterial(job, publisher),
  {
    connection: createQueueConnection(),
    // One at a time: generation is the heaviest consumer of the LLM quota
    // and running several in parallel simply produces rate limits.
    concurrency: 1,
  },
);

for (const [name, w] of [["document", worker], ["study", studyWorker]] as const) {
  w.on("failed", (job, err) => {
    logger.error(
      { queue: name, jobId: job?.id, attempts: job?.attemptsMade, err: err.message },
      "job failed",
    );
  });
}

worker.on("completed", (job) => {
  logger.info({ jobId: job.id }, "job completed");
});

studyWorker.on("completed", (job) => {
  logger.info({ jobId: job.id }, "generation completed");
});

logger.info(
  { queue: QUEUE_DOCUMENT_PROCESSING, concurrency: env.WORKER_CONCURRENCY },
  "worker listening",
);

async function shutdown(signal: string): Promise<void> {
  logger.info({ signal }, "shutting down");
  await Promise.allSettled([worker.close(), studyWorker.close()]);
  await Promise.allSettled([connection.quit(), publisher.quit()]);
  process.exit(0);
}

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    void shutdown(signal);
  });
}
