import { Redis } from "ioredis";
import { env } from "../env.js";

/**
 * BullMQ requires `maxRetriesPerRequest: null` on its connection so blocking
 * commands are not aborted mid-wait. This is separate from any general-purpose
 * Redis client.
 */
export function createQueueConnection(): Redis {
  return new Redis(env().REDIS_URL, {
    maxRetriesPerRequest: null,
    enableReadyCheck: false,
  });
}
