import { Hono } from "hono";
import { and, asc, desc, eq, inArray } from "drizzle-orm";
import { z } from "zod";
import {
  attemptAnswers,
  documentChunks,
  documents,
  testAttempts,
  testQuestions,
  tests,
} from "@examprep/db";
import { db } from "../lib/db.js";
import { validate } from "../lib/validate.js";
import { study } from "../lib/queue.js";
import { gradeAnswers } from "../lib/rag-client.js";
import { logger } from "../lib/logger.js";
import { badRequest, conflict, notFound } from "../lib/errors.js";
import {
  currentLoginSessionId,
  currentUserId,
  requireAuth,
} from "../middleware/auth.js";
import type { AppEnv } from "../types.js";

const createSchema = z.object({
  documentId: z.string().uuid(),
  title: z.string().min(1).max(200).optional(),
  questionCount: z.coerce.number().int().min(1).max(50).default(10),
  types: z.array(z.enum(["mcq", "short_answer", "true_false"])).optional(),
  difficulty: z.enum(["easy", "mixed", "hard"]).default("mixed"),
});

const idParamSchema = z.object({ id: z.string().uuid() });

const submitSchema = z.object({
  answers: z
    .array(
      z.object({
        questionId: z.string().uuid(),
        response: z.string().max(4000).nullable(),
      }),
    )
    .min(1),
});

export const testRoutes = new Hono<AppEnv>()
  .use("*", requireAuth())

  /**
   * Creates a test and returns immediately.
   *
   * Generation is several rate-limited model calls, so it runs as a job and
   * progress arrives over the WebSocket rather than the request being held
   * open for minutes.
   */
  .post("/", validate("json", createSchema), async (c) => {
    const userId = currentUserId(c);
    const { documentId, title, questionCount, types, difficulty } =
      c.req.valid("json");

    const [document] = await db()
      .select({
        id: documents.id,
        filename: documents.filename,
        status: documents.status,
      })
      .from(documents)
      .where(and(eq(documents.id, documentId), eq(documents.loginSessionId, currentLoginSessionId(c))))
      .limit(1);

    if (!document) {
      throw notFound("Document");
    }
    if (document.status !== "ready") {
      // Generating from a half-indexed document would silently cover only
      // part of it.
      throw conflict(
        `That document is still ${document.status}. Wait for it to finish processing.`,
      );
    }

    const [created] = await db()
      .insert(tests)
      .values({
        userId,
        loginSessionId: currentLoginSessionId(c),
        documentId,
        title: title ?? `Test on ${document.filename}`,
        status: "pending",
        config: { questionCount, types: types ?? null, difficulty },
      })
      .returning({ id: tests.id, title: tests.title });

    if (!created) {
      throw new Error("Failed to create test");
    }

    // Enqueue failures would otherwise leave a row stuck at 'pending'
    // forever, with nothing to advance it.
    let job;
    try {
      job = await study().add(
        "generate",
        {
          kind: "test",
          targetId: created.id,
          userId,
          documentId,
          count: questionCount,
          ...(types ? { questionTypes: types } : {}),
          difficulty,
        },
        // BullMQ rejects ":" in a custom job id.
        { jobId: `test-${created.id}` },
      );
    } catch (err) {
      await db().delete(tests).where(eq(tests.id, created.id));
      logger.error({ err, id: created.id }, "failed to queue test");
      throw err;
    }

    logger.info({ testId: created.id, jobId: job.id }, "test generation queued");
    return c.json(
      { testId: created.id, title: created.title, status: "pending" },
      202,
    );
  })

  .get("/", async (c) => {
    const rows = await db()
      .select()
      .from(tests)
      .where(eq(tests.loginSessionId, currentLoginSessionId(c)))
      .orderBy(desc(tests.createdAt))
      .limit(50);

    return c.json({ tests: rows });
  })

  /**
   * The paper as the student sits it.
   *
   * Correct answers and explanations are deliberately withheld: they arrive
   * with the results, and sending them here would put the answer key in the
   * browser before the test is taken.
   */
  .get("/:id", validate("param", idParamSchema), async (c) => {
    const testId = c.req.valid("param").id;

    const [test] = await db()
      .select()
      .from(tests)
      .where(and(eq(tests.id, testId), eq(tests.loginSessionId, currentLoginSessionId(c))))
      .limit(1);

    if (!test) {
      throw notFound("Test");
    }

    const questions = await db()
      .select({
        id: testQuestions.id,
        position: testQuestions.position,
        type: testQuestions.type,
        prompt: testQuestions.prompt,
        options: testQuestions.options,
      })
      .from(testQuestions)
      .where(eq(testQuestions.testId, testId))
      .orderBy(asc(testQuestions.position));

    return c.json({ test, questions });
  })

  .post("/:id/attempts", validate("param", idParamSchema), async (c) => {
    const userId = currentUserId(c);
    const testId = c.req.valid("param").id;

    const [test] = await db()
      .select({ id: tests.id, status: tests.status, count: tests.questionCount })
      .from(tests)
      .where(and(eq(tests.id, testId), eq(tests.loginSessionId, currentLoginSessionId(c))))
      .limit(1);

    if (!test) {
      throw notFound("Test");
    }
    if (test.status !== "ready") {
      throw conflict(`That test is ${test.status}; it cannot be started yet.`);
    }

    const [attempt] = await db()
      .insert(testAttempts)
      .values({
        testId,
        userId,
        status: "in_progress",
        maxScore: test.count,
      })
      .returning();

    return c.json({ attempt }, 201);
  })

  /**
   * Submits and grades an attempt in one step.
   *
   * Multiple choice and true/false are compared without a model; only short
   * answers are judged, and those go in one batched call.
   */
  .post(
    "/attempts/:id/submit",
    validate("param", idParamSchema),
    validate("json", submitSchema),
    async (c) => {
      const userId = currentUserId(c);
      const attemptId = c.req.valid("param").id;
      const { answers } = c.req.valid("json");

      const [attempt] = await db()
        .select()
        .from(testAttempts)
        .where(
          and(eq(testAttempts.id, attemptId), eq(testAttempts.userId, userId)),
        )
        .limit(1);

      if (!attempt) {
        throw notFound("Attempt");
      }
      if (attempt.status === "graded") {
        // Regrading would let a student resubmit after seeing the answers.
        throw conflict("This attempt has already been graded.");
      }
      if (attempt.status === "submitted") {
        throw conflict("This attempt is already being graded.");
      }

      const questions = await db()
        .select()
        .from(testQuestions)
        .where(eq(testQuestions.testId, attempt.testId))
        .orderBy(asc(testQuestions.position));

      if (questions.length === 0) {
        throw conflict("That test has no questions.");
      }

      const byId = new Map(questions.map((q) => [q.id, q]));
      const unknown = answers.filter((a) => !byId.has(a.questionId));
      if (unknown.length > 0) {
        throw badRequest("An answer referenced a question not in this test");
      }

      // Source passages are fetched so grading feedback can quote what the
      // material actually says rather than inventing a justification.
      const chunkIds = questions
        .map((q) => q.sourceChunkId)
        .filter((id): id is string => id !== null);
      const chunks = chunkIds.length
        ? await db()
            .select({ id: documentChunks.id, text: documentChunks.text })
            .from(documentChunks)
            .where(inArray(documentChunks.id, chunkIds))
        : [];
      const chunkText = new Map(chunks.map((ch) => [ch.id, ch.text]));

      const responses = new Map(answers.map((a) => [a.questionId, a.response]));

      // Claimed before grading, in one conditional update: of two submits
      // racing each other, exactly one moves the attempt out of in_progress.
      // Checking the status and grading afterwards let both through, each
      // grading and writing its own answers.
      const now = new Date();
      const [claimed] = await db()
        .update(testAttempts)
        .set({ status: "submitted", submittedAt: now })
        .where(
          and(
            eq(testAttempts.id, attemptId),
            eq(testAttempts.userId, userId),
            eq(testAttempts.status, "in_progress"),
          ),
        )
        .returning({ id: testAttempts.id });
      if (!claimed) {
        throw conflict("This attempt has already been submitted.");
      }

      let result: Awaited<ReturnType<typeof gradeAnswers>>;
      try {
        result = await gradeAnswers(
          userId,
          questions.map((q) => ({
            question_id: q.id,
            question_type: q.type,
            prompt: q.prompt,
            correct_answer: q.correctAnswer,
            explanation: q.explanation,
            // A question left blank is graded as blank rather than skipped, so
            // the score denominator stays the whole paper.
            response: responses.get(q.id) ?? null,
            options: q.options,
            source_text: q.sourceChunkId
              ? (chunkText.get(q.sourceChunkId) ?? "")
              : "",
          })),
          { correlationId: attemptId },
        );

        await db().transaction(async (tx) => {
          await tx
            .delete(attemptAnswers)
            .where(eq(attemptAnswers.attemptId, attemptId));

          await tx.insert(attemptAnswers).values(
            result.graded.map((g) => ({
              attemptId,
              questionId: g.question_id,
              response: responses.get(g.question_id) ?? null,
              isCorrect: g.is_correct,
              awarded: g.awarded,
              feedback: g.feedback,
            })),
          );

          await tx
            .update(testAttempts)
            .set({
              status: "graded",
              score: result.score,
              maxScore: result.max_score,
              gradedAt: new Date(),
            })
            .where(eq(testAttempts.id, attemptId));
        });
      } catch (err) {
        // Handed back, so a grading that failed -- the model rate limited,
        // the service restarting -- can be submitted again.
        await db()
          .update(testAttempts)
          .set({ status: "in_progress", submittedAt: null })
          .where(
            and(
              eq(testAttempts.id, attemptId),
              eq(testAttempts.status, "submitted"),
            ),
          )
          .catch((releaseErr: unknown) => {
            // Reported, not thrown: the grading failure is the error that
            // explains what happened.
            logger.error({ err: releaseErr, attemptId }, "could not release attempt");
          });
        throw err;
      }

      return c.json({
        attemptId,
        score: result.score,
        maxScore: result.max_score,
        results: result.graded.map((g) => {
          const question = byId.get(g.question_id)!;
          return {
            questionId: g.question_id,
            prompt: question.prompt,
            type: question.type,
            options: question.options,
            yourAnswer: responses.get(g.question_id) ?? null,
            // Released only now that the attempt is graded.
            correctAnswer: question.correctAnswer,
            isCorrect: g.is_correct,
            awarded: g.awarded,
            feedback: g.feedback,
            sourceChunkId: question.sourceChunkId,
          };
        }),
      });
    },
  )

  .get("/attempts/:id", validate("param", idParamSchema), async (c) => {
    const userId = currentUserId(c);
    const attemptId = c.req.valid("param").id;

    const [attempt] = await db()
      .select()
      .from(testAttempts)
      .where(
        and(eq(testAttempts.id, attemptId), eq(testAttempts.userId, userId)),
      )
      .limit(1);

    if (!attempt) {
      throw notFound("Attempt");
    }

    const rows = await db()
      .select()
      .from(attemptAnswers)
      .where(eq(attemptAnswers.attemptId, attemptId));

    return c.json({ attempt, answers: rows });
  })

  .delete("/:id", validate("param", idParamSchema), async (c) => {
    const testId = c.req.valid("param").id;

    const [test] = await db()
      .select({ id: tests.id })
      .from(tests)
      .where(and(eq(tests.id, testId), eq(tests.loginSessionId, currentLoginSessionId(c))))
      .limit(1);

    if (!test) {
      throw notFound("Test");
    }

    await db().delete(tests).where(eq(tests.id, testId));
    return c.body(null, 204);
  });
