import { Queue } from "bullmq";
import {
  QUEUE_DOCUMENT_PROCESSING,
  QUEUE_STUDY_GENERATION,
  type DocumentProcessingJob,
  type StudyGenerationJob,
} from "@examprep/shared";
import { createQueueConnection } from "./lib/connection.js";

/**
 * Queue definitions live here so producer (API) and consumer (worker) share one
 * source of truth for names, retry policy and backoff.
 */
export function createStudyQueue(): Queue<StudyGenerationJob> {
  return new Queue<StudyGenerationJob>(QUEUE_STUDY_GENERATION, {
    connection: createQueueConnection(),
    defaultJobOptions: {
      // Fewer attempts than ingestion: a generation failure is usually a
      // quota ceiling, and retrying hard makes that worse.
      attempts: 2,
      backoff: { type: "exponential", delay: 15_000 },
      removeOnComplete: { age: 86_400, count: 200 },
      removeOnFail: { age: 604_800 },
    },
  });
}

export function createDocumentQueue(): Queue<DocumentProcessingJob> {
  return new Queue<DocumentProcessingJob>(QUEUE_DOCUMENT_PROCESSING, {
    connection: createQueueConnection(),
    defaultJobOptions: {
      attempts: 3,
      backoff: { type: "exponential", delay: 5_000 },
      removeOnComplete: { age: 86_400, count: 500 },
      removeOnFail: { age: 604_800 },
    },
  });
}
