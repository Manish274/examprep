import type { Redis } from "ioredis";
import {
  documentChannel,
  generationChannel,
  type DocumentProgress,
  type GenerationProgress,
} from "@examprep/shared";

/**
 * Progress travels worker → Redis pub/sub → API WebSocket hub → browser.
 * The worker never holds a socket to the student; it only publishes, in the
 * shapes the hub validates before forwarding.
 */
export async function publishDocumentProgress(
  redis: Redis,
  message: DocumentProgress,
): Promise<void> {
  await redis.publish(documentChannel(message.documentId), JSON.stringify(message));
}

export async function publishGenerationProgress(
  redis: Redis,
  message: GenerationProgress,
): Promise<void> {
  await redis.publish(generationChannel(message.targetId), JSON.stringify(message));
}
