import type { Redis } from "ioredis";
import { documentChannel, type JobProgress } from "@examprep/shared";

/**
 * Progress travels worker → Redis pub/sub → API WebSocket hub → browser.
 * The worker never holds a socket to the student; it only publishes.
 */
export async function publishProgress(
  redis: Redis,
  documentId: string,
  progress: JobProgress,
): Promise<void> {
  await redis.publish(
    documentChannel(documentId),
    JSON.stringify({ documentId, progress }),
  );
}

/** Maps a stage to the percentage band it occupies in the overall pipeline. */
export const STAGE_WEIGHTS: Record<JobProgress["stage"], [number, number]> = {
  parsing: [0, 25],
  chunking: [25, 40],
  embedding: [40, 80],
  indexing: [80, 99],
  completed: [100, 100],
  failed: [0, 0],
};

/** Converts within-stage progress into an overall 0-100 percentage. */
export function overallPercent(
  stage: JobProgress["stage"],
  current: number,
  total: number,
): number {
  const band = STAGE_WEIGHTS[stage];
  if (total <= 0) return band[0];
  const ratio = Math.min(Math.max(current / total, 0), 1);
  return Math.round(band[0] + (band[1] - band[0]) * ratio);
}
