import type { WSContext } from "hono/ws";
import { and, eq } from "drizzle-orm";
import { documents, flashcardSets, tests } from "@examprep/db";
import {
  clientEventSchema,
  documentChannel,
  documentProgressSchema,
  generationChannel,
  generationProgressSchema,
  type ClientEvent,
} from "@examprep/shared";
import { Redis } from "ioredis";
import { db } from "../lib/db.js";
import { logger } from "../lib/logger.js";
import { verifyAccessToken } from "../lib/auth.js";
import { isLoginSessionLive } from "../lib/login-sessions.js";
import { env } from "../env.js";
import { handleChat, type ChatConnection } from "./chat.js";
import { fail, send } from "./frames.js";

/**
 * The WebSocket endpoint: authentication, frame ordering, progress relayed
 * from the worker, and dispatch -- questions are answered in `chat.ts`.
 */

/**
 * One connected browser.
 *
 * A connection starts unauthenticated and is closed if no valid auth frame
 * arrives promptly. Putting the token in the URL instead would leave a live
 * credential in access logs and referrer headers.
 */
interface Connection extends ChatConnection {
  /** Documents this connection wants progress for. */
  watching: Set<string>;
  /** Tests and flashcard sets this connection wants progress for. */
  watchingGeneration: Set<string>;
  authTimer: NodeJS.Timeout | null;
  /**
   * Serialises this connection's frames.
   *
   * Nothing awaits one `onMessage` before the next fires, so without this a
   * client that sends its auth frame and its first question together races:
   * the question is dispatched while the token is still being verified and is
   * rejected with "send an auth frame first". Two questions in quick
   * succession are worse -- both generate at once, and the second overwrites
   * the abort controller of the first, so cancelling stops only one of them
   * and the other streams on unread.
   *
   * Chaining also gives the second question the first one's answer in its
   * history, which is what a student typing quickly expects.
   */
  queue: Promise<void>;
  pending: number;
}

/** A client this far ahead of itself is malfunctioning, not impatient. */
const MAX_PENDING_FRAMES = 16;

const AUTH_TIMEOUT_MS = 10_000;

const connections = new Map<WSContext, Connection>();

// One subscriber for the whole process. A subscription per connection would
// open a Redis connection per browser tab.
let subscriber: Redis | null = null;

/**
 * Relays worker progress from Redis onto every socket watching it.
 *
 * The worker cannot hold a socket to the student -- it is a separate process
 * and the student may not even be connected -- so it publishes, and this
 * forwards. Each message is checked against the shared schema on the way
 * through: the browser is told only what the protocol promises it.
 */
function ensureSubscriber(): Redis {
  if (subscriber) return subscriber;

  subscriber = new Redis(env().REDIS_URL, { maxRetriesPerRequest: null });
  void subscriber.psubscribe("doc:progress:*", "gen:progress:*");

  subscriber.on("pmessage", (_pattern, channel, payload) => {
    let message: unknown;
    try {
      message = JSON.parse(payload);
    } catch {
      return;
    }

    const document = documentProgressSchema.safeParse(message);
    if (document.success && channel === documentChannel(document.data.documentId)) {
      for (const connection of connections.values()) {
        if (!connection.watching.has(document.data.documentId)) continue;
        send(connection.socket, { type: "document:progress", ...document.data });
      }
      return;
    }

    const generation = generationProgressSchema.safeParse(message);
    if (generation.success && channel === generationChannel(generation.data.targetId)) {
      for (const connection of connections.values()) {
        if (!connection.watchingGeneration.has(generation.data.targetId)) continue;
        send(connection.socket, { type: "generation:progress", ...generation.data });
      }
    }
  });

  return subscriber;
}

/**
 * Whether this sign-in may follow a document's progress.
 *
 * Progress carries the filename and the reason a file failed, so watching one
 * is scoped exactly like reading it.
 */
async function ownsDocument(loginSessionId: string, documentId: string): Promise<boolean> {
  const [row] = await db()
    .select({ id: documents.id })
    .from(documents)
    .where(and(eq(documents.id, documentId), eq(documents.loginSessionId, loginSessionId)))
    .limit(1);
  return Boolean(row);
}

/** The same, for a test or flashcard set being generated. */
async function ownsGeneration(loginSessionId: string, targetId: string): Promise<boolean> {
  const [test] = await db()
    .select({ id: tests.id })
    .from(tests)
    .where(and(eq(tests.id, targetId), eq(tests.loginSessionId, loginSessionId)))
    .limit(1);
  if (test) return true;
  const [set] = await db()
    .select({ id: flashcardSets.id })
    .from(flashcardSets)
    .where(
      and(eq(flashcardSets.id, targetId), eq(flashcardSets.loginSessionId, loginSessionId)),
    )
    .limit(1);
  return Boolean(set);
}

async function dispatch(
  connection: Connection,
  event: ClientEvent,
): Promise<void> {
  if (event.type === "auth") {
    const claims = await verifyAccessToken(event.token);
    if (!claims || !(await isLoginSessionLive(claims.sid))) {
      fail(connection.socket, "unauthorized", "Invalid or expired token");
      connection.socket.close(1008, "unauthorized");
      return;
    }
    connection.userId = claims.sub;
    connection.loginSessionId = claims.sid;
    if (connection.authTimer) {
      clearTimeout(connection.authTimer);
      connection.authTimer = null;
    }
    send(connection.socket, { type: "ready", userId: claims.sub });
    return;
  }

  if (event.type === "ping") {
    send(connection.socket, { type: "pong" });
    return;
  }

  // Everything below requires a completed handshake.
  const loginSessionId = connection.loginSessionId;
  if (!loginSessionId) {
    fail(connection.socket, "unauthorized", "Send an auth frame first");
    return;
  }

  switch (event.type) {
    case "subscribe:document":
      if (await ownsDocument(loginSessionId, event.documentId)) {
        connection.watching.add(event.documentId);
      } else {
        fail(connection.socket, "not_found", "Document not found");
      }
      break;
    case "unsubscribe:document":
      connection.watching.delete(event.documentId);
      break;
    case "subscribe:generation":
      if (await ownsGeneration(loginSessionId, event.targetId)) {
        connection.watchingGeneration.add(event.targetId);
      } else {
        fail(connection.socket, "not_found", "Nothing is being generated with that id");
      }
      break;
    case "cancel":
      // The student navigated away or stopped the answer; abandoning the
      // upstream request stops paying for tokens nobody will read.
      connection.abort?.abort();
      break;
    case "chat:send":
      await handleChat(connection, event);
      break;
  }
}

export function createHandlers() {
  return {
    onOpen(_evt: Event, ws: WSContext): void {
      ensureSubscriber();

      const connection: Connection = {
        socket: ws,
        userId: null,
        loginSessionId: null,
        watching: new Set(),
        watchingGeneration: new Set(),
        abort: null,
        authTimer: null,
        queue: Promise.resolve(),
        pending: 0,
      };
      connection.authTimer = setTimeout(() => {
        if (!connection.userId) {
          fail(ws, "unauthorized", "No auth frame received");
          ws.close(1008, "unauthorized");
        }
      }, AUTH_TIMEOUT_MS);

      connections.set(ws, connection);
    },

    async onMessage(evt: MessageEvent, ws: WSContext): Promise<void> {
      const connection = connections.get(ws);
      if (!connection) return;

      let parsed: unknown;
      try {
        parsed = JSON.parse(String(evt.data));
      } catch {
        fail(ws, "bad_request", "Frames must be JSON");
        return;
      }

      const result = clientEventSchema.safeParse(parsed);
      if (!result.success) {
        fail(ws, "bad_request", result.error.issues[0]?.message ?? "Invalid frame");
        return;
      }

      if (connection.pending >= MAX_PENDING_FRAMES) {
        fail(ws, "too_many_frames", "Too many messages in flight; slow down");
        return;
      }

      // Queued rather than awaited here: frames must be handled in the order
      // they arrived, and this handler is re-entered before the previous call
      // resolves.
      connection.pending += 1;
      connection.queue = connection.queue
        .then(() => dispatch(connection, result.data))
        .catch((err: unknown) => {
          logger.error({ err }, "websocket dispatch failed");
          fail(ws, "internal_error", "Something went wrong");
        })
        .finally(() => {
          connection.pending -= 1;
        });

      await connection.queue;
    },

    onClose(_evt: Event, ws: WSContext): void {
      const connection = connections.get(ws);
      if (connection?.authTimer) clearTimeout(connection.authTimer);
      // Stop generating for a student who is no longer listening.
      connection?.abort?.abort();
      connections.delete(ws);
    },
  };
}

/**
 * Drops every socket belonging to a sign-in that has just ended, so an open
 * tab stops streaming answers from material that is being deleted.
 */
export function disconnectLoginSession(loginSessionId: string): void {
  for (const connection of connections.values()) {
    if (connection.loginSessionId !== loginSessionId) continue;
    connection.abort?.abort();
    connection.socket.close(1008, "session ended");
  }
}

export async function closeHub(): Promise<void> {
  for (const connection of connections.values()) {
    if (connection.authTimer) clearTimeout(connection.authTimer);
    connection.abort?.abort();
  }
  connections.clear();
  if (subscriber) {
    await subscriber.quit();
    subscriber = null;
  }
}
