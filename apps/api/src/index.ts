import "./lib/env-file.js";
import { serve } from "@hono/node-server";

import { createApp } from "./app.js";
import { loadEnv } from "./env.js";
import { logger } from "./lib/logger.js";
import { closeDb } from "./db/index.js";
import { closeRedis } from "./lib/redis.js";

const env = loadEnv();
const app = createApp();

const server = serve(
  { fetch: app.fetch, port: env.API_PORT, hostname: env.API_HOST },
  (info) => {
    logger.info(
      { port: info.port, host: env.API_HOST, env: env.NODE_ENV },
      "api listening",
    );
  },
);

async function shutdown(signal: string): Promise<void> {
  logger.info({ signal }, "shutting down");
  server.close();
  await Promise.allSettled([closeDb(), closeRedis()]);
  process.exit(0);
}

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    void shutdown(signal);
  });
}
