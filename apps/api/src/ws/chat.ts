import type { WSContext } from "hono/ws";
import { and, desc, eq, inArray, ne } from "drizzle-orm";
import {
  chatSessions,
  documentChunks,
  messageSources,
  messages,
} from "@examprep/db";
import type { ClientEvent } from "@examprep/shared";
import { db } from "../lib/db.js";
import { logger } from "../lib/logger.js";
import { sessionDocumentIds } from "../lib/login-sessions.js";
import {
  ragChatStream,
  type ChatSource,
  type ChatStreamEvent,
} from "../lib/rag-client.js";
import { fail, send } from "./frames.js";

/**
 * Answering a question sent over the WebSocket: persist it, stream the
 * answer from the RAG service onto the socket, then persist the answer and
 * the citations it used.
 */

/** What answering needs from a connection. */
export interface ChatConnection {
  socket: WSContext;
  userId: string | null;
  /** The sign-in the connection authenticated as; chats are scoped by it. */
  loginSessionId: string | null;
  /** Cancels the answer currently streaming, if any. */
  abort: AbortController | null;
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

export async function handleChat(
  connection: ChatConnection,
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
            pageEnd: s.page_end,
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
