import "./lib/env-file.js";
import { serve } from "@hono/node-server";
import { createNodeWebSocket } from "@hono/node-ws";

import { createApp } from "./app.js";
import { loadEnv } from "./env.js";
import { logger } from "./lib/logger.js";
import { closeDb } from "./lib/db.js";
import { closeRedis } from "./lib/redis.js";
import { closeQueues } from "./lib/queue.js";
import { closeHub, createHandlers } from "./ws/hub.js";
import { startSessionSweeper } from "./lib/login-sessions.js";

const env = loadEnv();

const app = createApp();

// The upgrader is derived from the app instance and then bound to the server,
// so both have to exist before the /ws route can be declared.
const { injectWebSocket, upgradeWebSocket } = createNodeWebSocket({ app });
app.get("/ws", upgradeWebSocket(() => createHandlers()));

const server = serve(
  { fetch: app.fetch, port: env.API_PORT, hostname: env.API_HOST },
  (info) => {
    logger.info(
      { port: info.port, host: env.API_HOST, env: env.NODE_ENV },
      "api listening",
    );
  },
);

injectWebSocket(server);

// Clears sign-ins that ended without a sign-out -- a closed tab whose tokens
// have since lapsed -- along with their uploads.
const stopSweeper = startSessionSweeper();

async function shutdown(signal: string): Promise<void> {
  logger.info({ signal }, "shutting down");
  server.close();
  stopSweeper();
  await Promise.allSettled([closeHub(), closeQueues(), closeDb(), closeRedis()]);
  process.exit(0);
}

for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    void shutdown(signal);
  });
}
