import { Queue } from "bullmq";
import {
  QUEUE_DOCUMENT_PROCESSING,
  type DocumentProcessingJob,
} from "@examprep/shared";
import { createQueueConnection } from "./lib/connection.js";

/**
 * Queue definitions live here so producer (API) and consumer (worker) share one
 * source of truth for names, retry policy and backoff.
 */
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
