import { Queue } from "bullmq";
import type { Redis } from "ioredis";
import {
  QUEUE_DOCUMENT_PROCESSING,
  type DocumentProcessingJob,
} from "@examprep/shared";
import { createQueueConnection } from "./connection.js";

/**
 * Trying again at images the model could not read.
 *
 * An overloaded model answers "503, high demand" to a batch of four images,
 * and for a scanned document those four images are four pages. Left there,
 * the document was marked ready with the pages simply missing. Instead they
 * are tried again later, by a delayed job that reads only what is still
 * unread -- everything else comes from the vision cache.
 *
 * Delayed jobs rather than a wait inside this one, so the worker slot is free
 * for other uploads in the meantime.
 */

/**
 * Before each further try. An overload usually clears within minutes; three
 * tries over about a quarter of an hour ride it out.
 */
export const FIGURE_RETRY_DELAYS_MS = [60_000, 180_000, 600_000] as const;

export type FigureStep =
  | { kind: "complete" }
  | { kind: "retry"; attempt: number; delayMs: number }
  | { kind: "unread"; count: number };

/**
 * What to do about the images a pass left unread, given how many tries have
 * already been made after the first pass.
 */
export function nextFigureStep(
  unread: number,
  quotaSpent: boolean,
  retriesMade: number,
): FigureStep {
  if (unread <= 0) return { kind: "complete" };
  // A spent daily quota answers every try the same way until it resets,
  // which is hours away: the student is told instead.
  const delayMs = quotaSpent ? undefined : FIGURE_RETRY_DELAYS_MS[retriesMade];
  if (delayMs === undefined) return { kind: "unread", count: unread };
  return { kind: "retry", attempt: retriesMade + 1, delayMs };
}

let connection: Redis | null = null;
let queue: Queue<DocumentProcessingJob> | null = null;

export async function scheduleFigureRetry(
  data: DocumentProcessingJob,
  attempt: number,
  delayMs: number,
): Promise<void> {
  connection ??= createQueueConnection();
  queue ??= new Queue<DocumentProcessingJob>(QUEUE_DOCUMENT_PROCESSING, {
    connection,
  });
  await queue.add(
    "process",
    { ...data, figuresRetry: attempt },
    {
      // BullMQ rejects ":" in a custom job id.
      jobId: `${data.documentId}-figures-${attempt}`,
      delay: delayMs,
      // The delays above are the backoff; BullMQ retrying on top of them
      // would multiply it.
      attempts: 1,
      removeOnComplete: { age: 86_400, count: 500 },
      removeOnFail: { age: 604_800 },
    },
  );
}

export async function closeFigureRetries(): Promise<void> {
  await queue?.close();
  await connection?.quit();
  queue = null;
  connection = null;
}
