import { Hono } from "hono";
import { and, asc, desc, eq } from "drizzle-orm";
import { z } from "zod";
import { documents, flashcardSets, flashcards } from "@examprep/db";
import { db } from "../lib/db.js";
import { validate } from "../lib/validate.js";
import { study } from "../lib/queue.js";
import { logger } from "../lib/logger.js";
import { conflict, notFound } from "../lib/errors.js";
import { currentUserId, requireAuth } from "../middleware/auth.js";
import type { AppEnv } from "../types.js";

const createSchema = z.object({
  documentId: z.string().uuid(),
  title: z.string().min(1).max(200).optional(),
  cardCount: z.coerce.number().int().min(1).max(100).default(20),
});

const idParamSchema = z.object({ id: z.string().uuid() });

export const flashcardRoutes = new Hono<AppEnv>()
  .use("*", requireAuth())

  .post("/", validate("json", createSchema), async (c) => {
    const userId = currentUserId(c);
    const { documentId, title, cardCount } = c.req.valid("json");

    const [document] = await db()
      .select({
        id: documents.id,
        filename: documents.filename,
        status: documents.status,
      })
      .from(documents)
      .where(and(eq(documents.id, documentId), eq(documents.userId, userId)))
      .limit(1);

    if (!document) {
      throw notFound("Document");
    }
    if (document.status !== "ready") {
      throw conflict(
        `That document is still ${document.status}. Wait for it to finish processing.`,
      );
    }

    const [created] = await db()
      .insert(flashcardSets)
      .values({
        userId,
        documentId,
        title: title ?? `Flashcards for ${document.filename}`,
        status: "pending",
      })
      .returning({ id: flashcardSets.id, title: flashcardSets.title });

    if (!created) {
      throw new Error("Failed to create flashcard set");
    }

    // Enqueue failures would otherwise leave a row stuck at 'pending'
    // forever, with nothing to advance it.
    let job;
    try {
      job = await study().add(
        "generate",
        {
          kind: "flashcards",
          targetId: created.id,
          userId,
          documentId,
          count: cardCount,
        },
        { jobId: `cards-${created.id}` },
      );
    } catch (err) {
      await db().delete(flashcardSets).where(eq(flashcardSets.id, created.id));
      logger.error({ err, id: created.id }, "failed to queue flashcard set");
      throw err;
    }

    logger.info({ setId: created.id, jobId: job.id }, "flashcards queued");
    return c.json(
      { setId: created.id, title: created.title, status: "pending" },
      202,
    );
  })

  .get("/", async (c) => {
    const rows = await db()
      .select()
      .from(flashcardSets)
      .where(eq(flashcardSets.userId, currentUserId(c)))
      .orderBy(desc(flashcardSets.createdAt))
      .limit(50);

    return c.json({ sets: rows });
  })

  .get("/:id", validate("param", idParamSchema), async (c) => {
    const userId = currentUserId(c);
    const setId = c.req.valid("param").id;

    const [set] = await db()
      .select()
      .from(flashcardSets)
      .where(and(eq(flashcardSets.id, setId), eq(flashcardSets.userId, userId)))
      .limit(1);

    if (!set) {
      throw notFound("Flashcard set");
    }

    // Both sides are returned together: unlike a test, a flashcard has no
    // answer to withhold -- turning the card over is the whole interaction.
    const cards = await db()
      .select()
      .from(flashcards)
      .where(eq(flashcards.setId, setId))
      .orderBy(asc(flashcards.position));

    return c.json({ set, cards });
  })

  .delete("/:id", validate("param", idParamSchema), async (c) => {
    const userId = currentUserId(c);
    const setId = c.req.valid("param").id;

    const [set] = await db()
      .select({ id: flashcardSets.id })
      .from(flashcardSets)
      .where(and(eq(flashcardSets.id, setId), eq(flashcardSets.userId, userId)))
      .limit(1);

    if (!set) {
      throw notFound("Flashcard set");
    }

    await db().delete(flashcardSets).where(eq(flashcardSets.id, setId));
    return c.body(null, 204);
  });
