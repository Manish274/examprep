import { Hono } from "hono";
import { cors } from "hono/cors";
import { secureHeaders } from "hono/secure-headers";
import { errorHandler } from "./middleware/error-handler.js";
import { requestContext } from "./middleware/request-context.js";
import { healthRoutes } from "./routes/health.js";
import { authRoutes } from "./routes/auth.js";
import { documentRoutes } from "./routes/documents.js";
import { notFound } from "./lib/errors.js";
import type { AppEnv } from "./types.js";

/**
 * Builds the Hono app. Kept free of side effects and server binding so tests
 * can exercise routes through `app.request()` without opening a port.
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

  // Feature routers land here as milestones complete:
  //   /api/chat  /api/tests  /api/flashcards

  app.notFound(() => {
    throw notFound("Route");
  });
  app.onError(errorHandler);

  return app;
}

export type App = ReturnType<typeof createApp>;
