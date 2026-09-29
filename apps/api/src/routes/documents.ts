import { Hono } from "hono";
import { and, desc, eq, lt } from "drizzle-orm";
import { z } from "zod";
import { documentChunks, documents } from "@examprep/db";
import { validate } from "../lib/validate.js";
import { db } from "../lib/db.js";
import { detectKind, EXTENSION_FOR_KIND } from "../lib/file-type.js";
import { documents as documentQueue } from "../lib/queue.js";
import { sha256, storage, storageKeyFor } from "../lib/storage.js";
import { removeDocument } from "../lib/document-removal.js";
import { logger } from "../lib/logger.js";
import {
  badRequest,
  conflict,
  notFound,
  payloadTooLarge,
  unprocessable,
} from "../lib/errors.js";
import {
  currentLoginSessionId,
  currentUserId,
  requireAuth,
} from "../middleware/auth.js";
import { env } from "../env.js";
import type { AppEnv } from "../types.js";

const listQuerySchema = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(20),
  before: z.string().datetime().optional(),
});

const idParamSchema = z.object({ id: z.string().uuid() });

export const documentRoutes = new Hono<AppEnv>()
  .use("*", requireAuth())

  /**
   * Accepts an upload and returns immediately.
   *
   * Processing a large deck takes minutes under free-tier rate limits, so the
   * request only stores the file and enqueues a job. Progress reaches the
   * browser over the WebSocket, keyed by the returned document id.
   */
  .post("/", async (c) => {
    const userId = currentUserId(c);
    const loginSessionId = currentLoginSessionId(c);
    const { MAX_UPLOAD_BYTES } = env();

    const form = await c.req.formData().catch(() => null);
    if (!form) {
      throw badRequest("Expected a multipart/form-data body");
    }

    const file = form.get("file");
    if (!(file instanceof File)) {
      throw badRequest("Expected a file field named 'file'");
    }
    if (file.size === 0) {
      throw badRequest("Uploaded file is empty");
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      throw payloadTooLarge(
        `File exceeds the ${Math.floor(MAX_UPLOAD_BYTES / 1_048_576)}MB limit`,
      );
    }

    const data = Buffer.from(await file.arrayBuffer());

    // The declared content type and the filename extension are both caller
    // controlled, and this file is about to be handed to a parser. Trust the
    // bytes instead.
    const kind = detectKind(data);
    if (!kind) {
      throw unprocessable(
        "Unsupported file type. Upload a PDF or PowerPoint document.",
      );
    }

    const contentHash = sha256(data);
    const [duplicate] = await db()
      .select({
        id: documents.id,
        userId: documents.userId,
        storageKey: documents.storageKey,
        status: documents.status,
      })
      .from(documents)
      .where(
        and(
          eq(documents.loginSessionId, loginSessionId),
          eq(documents.contentHash, contentHash),
        ),
      )
      .limit(1);

    if (duplicate && duplicate.status === "failed") {
      // A failed row must not block the retry. Otherwise a document that hit a
      // bug -- or a rate limit -- is permanently un-uploadable: "we could not
      // process this" followed by "you have already uploaded this" is a dead
      // end with no way out but a database prompt. Removed properly rather
      // than just the row: a failure part way through indexing can leave a
      // stored file and some vectors behind.
      await removeDocument(duplicate);
      logger.info(
        { documentId: duplicate.id },
        "replacing a failed document with a fresh upload",
      );
    } else if (duplicate) {
      // Re-uploading the same bytes should return the existing document rather
      // than paying to embed it twice.
      throw conflict(
        `This document was already uploaded (id ${duplicate.id}, status ${duplicate.status})`,
      );
    }

    const [created] = await db()
      .insert(documents)
      .values({
        userId,
        loginSessionId,
        filename: file.name || `upload.${EXTENSION_FOR_KIND[kind]}`,
        kind,
        storageKey: "",
        byteSize: data.byteLength,
        contentHash,
        status: "queued",
      })
      .returning({ id: documents.id, filename: documents.filename });

    if (!created) {
      throw new Error("Failed to create document record");
    }

    const key = storageKeyFor(userId, created.id, EXTENSION_FOR_KIND[kind]);
    try {
      await storage().write(key, data);
      await db()
        .update(documents)
        .set({ storageKey: key, updatedAt: new Date() })
        .where(eq(documents.id, created.id));
    } catch (err) {
      // Without this the row would linger pointing at a file that was never
      // written, and its content hash would block re-upload forever.
      await db().delete(documents).where(eq(documents.id, created.id));
      throw err;
    }

    const job = await documentQueue().add(
      "process",
      {
        documentId: created.id,
        userId,
        storageKey: key,
        filename: created.filename,
        kind,
      },
      { jobId: created.id },
    );

    logger.info(
      { documentId: created.id, jobId: job.id, bytes: data.byteLength, kind },
      "document queued",
    );

    return c.json(
      { documentId: created.id, jobId: job.id ?? created.id, status: "queued" },
      202,
    );
  })

  .get("/", validate("query", listQuerySchema), async (c) => {
    const loginSessionId = currentLoginSessionId(c);
    const { limit, before } = c.req.valid("query");

    const rows = await db()
      .select({
        id: documents.id,
        filename: documents.filename,
        kind: documents.kind,
        byteSize: documents.byteSize,
        status: documents.status,
        pageCount: documents.pageCount,
        chunkCount: documents.chunkCount,
        figuresPending: documents.figuresPending,
        errorMessage: documents.errorMessage,
        createdAt: documents.createdAt,
        updatedAt: documents.updatedAt,
      })
      .from(documents)
      .where(
        before
          ? and(
              eq(documents.loginSessionId, loginSessionId),
              lt(documents.createdAt, new Date(before)),
            )
          : eq(documents.loginSessionId, loginSessionId),
      )
      .orderBy(desc(documents.createdAt))
      .limit(limit);

    const last = rows.at(-1);
    return c.json({
      documents: rows,
      nextCursor: rows.length === limit ? last?.createdAt.toISOString() : null,
    });
  })

  .get("/:id", validate("param", idParamSchema), async (c) => {
    const [row] = await db()
      .select()
      .from(documents)
      .where(
        and(
          eq(documents.id, c.req.valid("param").id),
          // Scoping every query by sign-in is what actually enforces
          // isolation -- between students, and between one student's visits.
          eq(documents.loginSessionId, currentLoginSessionId(c)),
        ),
      )
      .limit(1);

    if (!row) {
      throw notFound("Document");
    }
    return c.json({ document: row });
  })

  /** The chunks a document produced. Useful for inspecting ingestion quality. */
  .get("/:id/chunks", validate("param", idParamSchema), async (c) => {
    const documentId = c.req.valid("param").id;

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

    const rows = await db()
      .select()
      .from(documentChunks)
      .where(eq(documentChunks.documentId, documentId))
      .orderBy(documentChunks.chunkIndex);

    return c.json({ chunks: rows });
  })

  .delete("/:id", validate("param", idParamSchema), async (c) => {
    const [row] = await db()
      .select({
        id: documents.id,
        userId: documents.userId,
        storageKey: documents.storageKey,
      })
      .from(documents)
      .where(
        and(
          eq(documents.id, c.req.valid("param").id),
          eq(documents.loginSessionId, currentLoginSessionId(c)),
        ),
      )
      .limit(1);

    if (!row) {
      throw notFound("Document");
    }

    await removeDocument(row);
    return c.body(null, 204);
  });
