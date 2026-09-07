import { Queue } from "bullmq";
import { Redis } from "ioredis";
import {
  QUEUE_DOCUMENT_PROCESSING,
  QUEUE_STUDY_GENERATION,
  type DocumentProcessingJob,
  type StudyGenerationJob,
} from "@examprep/shared";
import { env } from "../env.js";

let connection: Redis | null = null;
let documentQueue: Queue<DocumentProcessingJob> | null = null;
let studyQueue: Queue<StudyGenerationJob> | null = null;

/**
 * BullMQ requires maxRetriesPerRequest: null so its blocking commands are not
 * aborted mid-wait. That is why this connection is separate from the general
 * purpose Redis client.
 */
function queueConnection(): Redis {
  connection ??= new Redis(env().REDIS_URL, {
    maxRetriesPerRequest: null,
    enableReadyCheck: false,
  });
  return connection;
}

/**
 * Retry policy lives with the producer so it is declared once. A failed
 * ingest is usually a transient provider or database problem, so three
 * attempts with growing backoff clears most of them without a person looking.
 */
export function documents(): Queue<DocumentProcessingJob> {
  documentQueue ??= new Queue<DocumentProcessingJob>(
    QUEUE_DOCUMENT_PROCESSING,
    {
      connection: queueConnection(),
      defaultJobOptions: {
        attempts: 3,
        backoff: { type: "exponential", delay: 5_000 },
        removeOnComplete: { age: 86_400, count: 500 },
        removeOnFail: { age: 604_800 },
      },
    },
  );
  return documentQueue;
}

/**
 * Generation jobs retry less than ingestion: a failure there is usually a
 * quota ceiling, and retrying hard makes that worse rather than better.
 */
export function study(): Queue<StudyGenerationJob> {
  studyQueue ??= new Queue<StudyGenerationJob>(QUEUE_STUDY_GENERATION, {
    connection: queueConnection(),
    defaultJobOptions: {
      attempts: 2,
      backoff: { type: "exponential", delay: 15_000 },
      removeOnComplete: { age: 86_400, count: 200 },
      removeOnFail: { age: 604_800 },
    },
  });
  return studyQueue;
}

export async function closeQueues(): Promise<void> {
  await Promise.allSettled([documentQueue?.close(), studyQueue?.close()]);
  documentQueue = null;
  studyQueue = null;
  if (connection) {
    await connection.quit();
    connection = null;
  }
}
