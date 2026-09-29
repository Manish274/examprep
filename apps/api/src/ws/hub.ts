import type { WSContext } from "hono/ws";
import { and, desc, eq, inArray, ne } from "drizzle-orm";
import {
  chatSessions,
  documentChunks,
  documents,
  flashcardSets,
  messageSources,
  messages,
  tests,
} from "@examprep/db";
import {
  clientEventSchema,
  documentChannel,
  documentProgressSchema,
  generationChannel,
  generationProgressSchema,
  type ClientEvent,
  type ServerEvent,
} from "@examprep/shared";
import { Redis } from "ioredis";
import { db } from "../lib/db.js";
import { logger } from "../lib/logger.js";
import { verifyAccessToken } from "../lib/auth.js";
import {
  isLoginSessionLive,
  sessionDocumentIds,
} from "../lib/login-sessions.js";
import {
  ragChatStream,
  type ChatSource,
  type ChatStreamEvent,
} from "../lib/rag-client.js";
import { env } from "../env.js";

/**
 * One connected browser.
 *
 * A connection starts unauthenticated and is closed if no valid auth frame
 * arrives promptly. Putting the token in the URL instead would leave a live
 * credential in access logs and referrer headers.
 */
interface Connection {
  socket: WSContext;
  userId: string | null;
  /** The sign-in the connection authenticated as; chats are scoped by it. */
  loginSessionId: string | null;
  /** Documents this connection wants progress for. */
  watching: Set<string>;
  /** Tests and flashcard sets this connection wants progress for. */
  watchingGeneration: Set<string>;
  /** Cancels the answer currently streaming, if any. */
  abort: AbortController | null;
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

function send(socket: WSContext, event: ServerEvent): void {
  try {
    socket.send(JSON.stringify(event));
  } catch (err) {
    logger.debug({ err }, "failed to write to socket");
  }
}

function fail(socket: WSContext, code: string, message: string): void {
  send(socket, { type: "error", code, message });
}

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

/**
 * How many past messages are fetched for context.
 *
 * More than the RAG service will actually use: it trims to a turn and token
 * budget of its own, and that policy belongs in one place -- next to the
 * prompt it has to fit inside.
 */
const HISTORY_MESSAGES = 20;

/**
 * The most recent turns of a conversation, oldest first.
 *
 * Ordering descending and reversing is the point. Ordering ascending with a
 * limit returns the *first* twenty messages, so once a conversation passed
 * twenty the context froze at its opening and every later question was
 * answered as though the intervening exchange had not happened -- the exact
 * failure this history exists to prevent, and invisible in a short test.
 *
 * Empty rows are excluded: an assistant row is written before its answer
 * streams, so a request in flight would otherwise contribute a blank turn.
 */
async function loadHistory(
  sessionId: string,
): Promise<{ role: string; content: string }[]> {
  const rows = await db()
    .select({ role: messages.role, content: messages.content })
    .from(messages)
    .where(and(eq(messages.sessionId, sessionId), ne(messages.content, "")))
    .orderBy(desc(messages.createdAt))
    .limit(HISTORY_MESSAGES);

  return rows.reverse().map((r) => ({ role: r.role, content: r.content }));
}

/**
 * Persists the citations an answer actually used.
 *
 * A source is only stored if its chunk still exists: a document deleted
 * mid-conversation would otherwise leave a foreign key pointing at nothing.
 */
async function persistSources(
  messageId: string,
  sources: ChatSource[],
): Promise<void> {
  if (sources.length === 0) return;

  const existing = await db()
    .select({ id: documentChunks.id })
    .from(documentChunks)
    .where(
      inArray(
        documentChunks.id,
        sources.map((s) => s.chunk_id),
      ),
    );

  const alive = new Set(existing.map((r) => r.id));
  const rows = sources
    .filter((s) => alive.has(s.chunk_id))
    .map((s, index) => ({
      messageId,
      chunkId: s.chunk_id,
      marker: s.marker,
      rank: index,
    }));

  if (rows.length > 0) {
    await db().insert(messageSources).values(rows);
  }
}

/**
 * A sidebar label from the opening question.
 *
 * Cut on a word boundary: "How do I handle words that never appea…" reads as
 * a title, "How do I handle words that never appea" reads as a bug.
 */
export function deriveTitle(question: string, maxLength = 60): string {
  const cleaned = question.replace(/\s+/g, " ").trim();
  if (cleaned.length <= maxLength) return cleaned || "New chat";

  const cut = cleaned.slice(0, maxLength);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > maxLength * 0.6 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`;
}

const NOTHING_UPLOADED_REPLY =
  "There is nothing uploaded in this session yet. Add a PDF or slide deck " +
  "with the + and ask again -- answers only come from your own material.";

/**
 * The answer when a session has no ready material.
 *
 * Answered here rather than by the RAG service: it reads an empty document list
 * as no filter at all, which would search every upload the student has ever
 * made -- including other sign-ins' -- instead of none.
 */
async function* nothingUploaded(): AsyncGenerator<ChatStreamEvent> {
  yield { type: "token", delta: NOTHING_UPLOADED_REPLY };
  yield { type: "done", unsupported: true, retrieved: 0, rewrittenQuery: null };
}

async function handleChat(
  connection: Connection,
  event: Extract<ClientEvent, { type: "chat:send" }>,
): Promise<void> {
  const { userId, loginSessionId } = connection;
  if (!userId || !loginSessionId) return;

  const [session] = await db()
    .select({
      id: chatSessions.id,
      documentId: chatSessions.documentId,
      title: chatSessions.title,
    })
    .from(chatSessions)
    .where(
      and(
        eq(chatSessions.id, event.sessionId),
        // Scoped by sign-in: a chat id alone must not reach another
        // student's conversation, or one from an earlier visit.
        eq(chatSessions.userId, userId),
        eq(chatSessions.loginSessionId, loginSessionId),
      ),
    )
    .limit(1);

  if (!session) {
    fail(connection.socket, "not_found", "Chat session not found");
    return;
  }

  const history = await loadHistory(session.id);

  // A chat over "everything" means everything uploaded in this sign-in. The
  // RAG service scopes by user alone, so the documents are named here.
  const documentIds = session.documentId
    ? [session.documentId]
    : await sessionDocumentIds(loginSessionId);

  await db().insert(messages).values({
    sessionId: session.id,
    role: "user",
    content: event.content,
    mode: event.mode,
  });

  if (!session.title) {
    // Named from the question that opened it. A model could write a better
    // title, but every generation call comes out of a small daily quota that
    // the answers themselves need -- and an untitled row in the sidebar is a
    // worse outcome than a plainly truncated one.
    await db()
      .update(chatSessions)
      .set({ title: deriveTitle(event.content) })
      .where(eq(chatSessions.id, session.id));
  }

  const [assistant] = await db()
    .insert(messages)
    .values({
      sessionId: session.id,
      role: "assistant",
      // Filled in as the answer streams; an empty row now means a reload
      // mid-answer still shows the turn rather than losing it.
      content: "",
      mode: event.mode,
    })
    .returning({ id: messages.id });

  if (!assistant) {
    fail(connection.socket, "internal_error", "Could not start the answer");
    return;
  }

  send(connection.socket, {
    type: "chat:start",
    sessionId: session.id,
    messageId: assistant.id,
  });

  const controller = new AbortController();
  connection.abort = controller;

  const started = Date.now();
  const buffer: string[] = [];
  let sources: ChatSource[] = [];
  let unsupported = false;
  let retrieved = 0;
  let rewritten: string | null = null;

  try {
    const stream =
      documentIds.length === 0
        ? nothingUploaded()
        : ragChatStream(
            {
              question: event.content,
              userId,
              mode: event.mode,
              documentIds,
              history,
            },
            { correlationId: assistant.id, signal: controller.signal },
          );
    for await (const chunk of stream) {
      if (chunk.type === "stage") {
        send(connection.socket, {
          type: "chat:stage",
          sessionId: session.id,
          messageId: assistant.id,
          stage: chunk.stage,
          ...(chunk.passages !== undefined ? { passages: chunk.passages } : {}),
        });
      } else if (chunk.type === "token") {
        buffer.push(chunk.delta);
        send(connection.socket, {
          type: "chat:token",
          sessionId: session.id,
          messageId: assistant.id,
          delta: chunk.delta,
        });
      } else if (chunk.type === "sources") {
        sources = chunk.sources;
        send(connection.socket, {
          type: "chat:sources",
          sessionId: session.id,
          messageId: assistant.id,
          sources: chunk.sources.map((s) => ({
            marker: s.marker,
            chunkId: s.chunk_id,
            documentId: s.document_id,
            documentName: s.document_name,
            pageNumber: s.page_number,
            slideNumber: s.slide_number,
            headingPath: s.heading_path,
            snippet: s.snippet,
          })),
        });
      } else if (chunk.type === "done") {
        unsupported = chunk.unsupported;
        retrieved = chunk.retrieved;
        rewritten = chunk.rewrittenQuery;
      } else {
        fail(connection.socket, "generation_failed", chunk.message);
      }
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    logger.error({ err: message, sessionId: session.id }, "chat stream failed");
    fail(connection.socket, "generation_failed", message.slice(0, 300));
  } finally {
    connection.abort = null;
  }

  const answer = buffer.join("").trim();

  if (!answer) {
    // Generation produced nothing -- an upstream failure, or the student
    // cancelled. Leaving the empty row behind would put a blank assistant turn
    // into the history that every later question is condensed against, which
    // actively degrades the next answer. Removing it is more honest: the turn
    // did not happen.
    await db().delete(messages).where(eq(messages.id, assistant.id));
    send(connection.socket, {
      type: "chat:done",
      sessionId: session.id,
      messageId: assistant.id,
      unsupported: true,
      retrieved,
    });
    return;
  }

  await db()
    .update(messages)
    .set({
      content: answer,
      unsupported,
      rewrittenQuery: rewritten,
      latencyMs: Date.now() - started,
    })
    .where(eq(messages.id, assistant.id));

  await persistSources(assistant.id, sources);
  await db()
    .update(chatSessions)
    .set({ updatedAt: new Date() })
    .where(eq(chatSessions.id, session.id));

  send(connection.socket, {
    type: "chat:done",
    sessionId: session.id,
    messageId: assistant.id,
    unsupported,
    retrieved,
  });
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
