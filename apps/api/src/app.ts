import { Hono } from "hono";
import { cors } from "hono/cors";
import { secureHeaders } from "hono/secure-headers";
import { errorHandler } from "./middleware/error-handler.js";
import { requestContext } from "./middleware/request-context.js";
import { healthRoutes } from "./routes/health.js";
import { authRoutes } from "./routes/auth.js";
import { documentRoutes } from "./routes/documents.js";
import { chatRoutes } from "./routes/chat.js";
import { testRoutes } from "./routes/tests.js";
import { flashcardRoutes } from "./routes/flashcards.js";
import { traceRoutes } from "./routes/traces.js";
import { notFound } from "./lib/errors.js";
import type { AppEnv } from "./types.js";

/**
 * Builds the Hono app. Kept free of side effects and server binding so tests
 * can exercise routes through `app.request()` without opening a port.
 */
/**
 * Builds the REST app.
 *
 * The WebSocket route is added by the entrypoint rather than here: the
 * upgrader has to be constructed from this app instance and then bound to the
 * Node server, neither of which exists yet. Tests therefore get a REST-only
 * app, which is what they want anyway.
 */
export function createApp() {
  const app = new Hono<AppEnv>();

  app.use("*", requestContext());
  app.use("*", secureHeaders());
  app.use(
    "*",
    cors({
      origin: ["http://localhost:3000"],
      credentials: true,
      allowHeaders: ["content-type", "authorization", "x-request-id"],
    }),
  );

  app.route("/", healthRoutes);
  app.route("/api/auth", authRoutes);
  app.route("/api/documents", documentRoutes);
  app.route("/api/chat", chatRoutes);
  app.route("/api/tests", testRoutes);
  app.route("/api/flashcards", flashcardRoutes);
  app.route("/api/traces", traceRoutes);

  app.notFound(() => {
    throw notFound("Route");
  });
  app.onError(errorHandler);

  return app;
}

export type App = ReturnType<typeof createApp>;
