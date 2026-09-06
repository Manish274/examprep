import { Redis } from "ioredis";
import { env } from "../env.js";

let singleton: Redis | null = null;

/**
 * Shared Redis connection for general use. BullMQ requires its own connection
 * with `maxRetriesPerRequest: null`, so queues create theirs separately.
 */
export function redis(): Redis {
  singleton ??= new Redis(env().REDIS_URL, {
    lazyConnect: false,
    maxRetriesPerRequest: 3,
  });
  return singleton;
}

export async function closeRedis(): Promise<void> {
  if (singleton) {
    await singleton.quit();
    singleton = null;
  }
}
