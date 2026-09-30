import { Hono } from "hono";
import { and, asc, desc, eq, inArray } from "drizzle-orm";
import { z } from "zod";
import {
  chatSessions,
  documentChunks,
  documents,
  messageSources,
  messages,
} from "@examprep/db";
import { db } from "../lib/db.js";
import { validate } from "../lib/validate.js";
import { notFound } from "../lib/errors.js";
import {
  currentLoginSessionId,
  currentUserId,
  requireAuth,
} from "../middleware/auth.js";
import type { AppEnv } from "../types.js";

const createSessionSchema = z.object({
  /** Omit to search everything the student owns. */
  documentId: z.string().uuid().optional(),
  title: z.string().min(1).max(200).optional(),
});

const idParamSchema = z.object({ id: z.string().uuid() });

/**
 * REST for chat history. The conversation itself runs over the WebSocket --
 * these endpoints exist so a reloaded page can rebuild what was said.
 */
export const chatRoutes = new Hono<AppEnv>()
  .use("*", requireAuth())

  .post("/sessions", validate("json", createSessionSchema), async (c) => {
    const userId = currentUserId(c);
    const { documentId, title } = c.req.valid("json");

    if (documentId) {
      // Checked here rather than at first message, so a session is never
      // created pointing at a document the student cannot reach.
      const [owned] = await db()
        .select({ id: documents.id })
        .from(documents)
        .where(
          and(
            eq(documents.id, documentId),
            eq(documents.loginSessionId, currentLoginSessionId(c)),
          ),
        )
        .limit(1);
      if (!owned) {
        throw notFound("Document");
      }
    }

    const [created] = await db()
      .insert(chatSessions)
      .values({
        userId,
        loginSessionId: currentLoginSessionId(c),
        documentId: documentId ?? null,
        title: title ?? null,
      })
      .returning();

    return c.json({ session: created }, 201);
  })

  .get("/sessions", async (c) => {
    const rows = await db()
      .select()
      .from(chatSessions)
      // Only this sign-in's chats: each login starts with an empty sidebar.
      .where(eq(chatSessions.loginSessionId, currentLoginSessionId(c)))
      .orderBy(desc(chatSessions.updatedAt))
      .limit(50);

    return c.json({ sessions: rows });
  })

  .get("/sessions/:id", validate("param", idParamSchema), async (c) => {
    const [session] = await db()
      .select()
      .from(chatSessions)
      .where(
        and(
          eq(chatSessions.id, c.req.valid("param").id),
          eq(chatSessions.loginSessionId, currentLoginSessionId(c)),
        ),
      )
      .limit(1);

    if (!session) {
      throw notFound("Session");
    }
    return c.json({ session });
  })

  .get("/sessions/:id/messages", validate("param", idParamSchema), async (c) => {
    const sessionId = c.req.valid("param").id;

    const [session] = await db()
      .select({ id: chatSessions.id })
      .from(chatSessions)
      .where(
        and(eq(chatSessions.id, sessionId), eq(chatSessions.loginSessionId, currentLoginSessionId(c))),
      )
      .limit(1);

    if (!session) {
      throw notFound("Session");
    }

    const rows = await db()
      .select()
      .from(messages)
      .where(eq(messages.sessionId, sessionId))
      .orderBy(asc(messages.createdAt));

    // Citations are fetched in one query and grouped, rather than one query
    // per message, which would be a round trip per turn on every page load.
    //
    // Joined out to the chunk and its document on purpose. The stored row is
    // only a marker and a chunk id, so returning it raw would render a
    // reloaded conversation with bare "[S1]" markers and nothing to say what
    // they point at -- while the same answer, live over the WebSocket, shows
    // the document, page and heading. A citation that survives a refresh is
    // the whole reason they are persisted.
    const sources = rows.length
      ? await db()
          .select({
            messageId: messageSources.messageId,
            marker: messageSources.marker,
            chunkId: messageSources.chunkId,
            documentId: documentChunks.documentId,
            documentName: documents.filename,
            pageNumber: documentChunks.pageNumber,
            pageEnd: documentChunks.pageEnd,
            slideNumber: documentChunks.slideNumber,
            headingPath: documentChunks.headingPath,
            snippet: documentChunks.text,
          })
          .from(messageSources)
          .innerJoin(
            documentChunks,
            eq(documentChunks.id, messageSources.chunkId),
          )
          .innerJoin(documents, eq(documents.id, documentChunks.documentId))
          .where(
            inArray(
              messageSources.messageId,
              rows.map((m) => m.id),
            ),
          )
          .orderBy(asc(messageSources.rank))
      : [];

    const byMessage = new Map<string, typeof sources>();
    for (const source of sources) {
      const list = byMessage.get(source.messageId) ?? [];
      list.push(source);
      byMessage.set(source.messageId, list);
    }

    return c.json({
      messages: rows.map((m) => ({
        ...m,
        sources: (byMessage.get(m.id) ?? []).map((source) => ({
          ...source,
          // Trimmed here rather than in the browser: a citation needs enough
          // to recognise the passage, not the whole chunk on every message.
          snippet: source.snippet.slice(0, 320),
        })),
      })),
    });
  })

  .delete("/sessions/:id", validate("param", idParamSchema), async (c) => {
    const sessionId = c.req.valid("param").id;

    const [session] = await db()
      .select({ id: chatSessions.id })
      .from(chatSessions)
      .where(
        and(eq(chatSessions.id, sessionId), eq(chatSessions.loginSessionId, currentLoginSessionId(c))),
      )
      .limit(1);

    if (!session) {
      throw notFound("Session");
    }

    // Messages and their sources cascade with the session.
    await db().delete(chatSessions).where(eq(chatSessions.id, sessionId));
    return c.body(null, 204);
  });
