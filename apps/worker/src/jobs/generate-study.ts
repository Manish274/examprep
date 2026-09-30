import type { Job } from "bullmq";
import type { Redis } from "ioredis";
import { and, eq, isNotNull, ne, sql } from "drizzle-orm";
import {
  flashcardSets,
  flashcards,
  testQuestions,
  tests,
} from "@examprep/db";
import {
  studyGenerationJobSchema,
  type GenerationProgress,
  type StudyGenerationJob,
} from "@examprep/shared";
import { db } from "../lib/db.js";
import { logger } from "../lib/logger.js";
import { publishGenerationProgress } from "../lib/progress.js";
import {
  generateFlashcards,
  generateTest,
  RagPermanentError,
} from "../lib/rag-client.js";

/**
 * The chunks earlier quizzes and card sets on this document were written
 * from, so the next one is written from the rest first. Without it a quiz and
 * a set of flashcards on the same notes ask the same things.
 */
async function chunksAlreadyUsed(
  documentId: string,
  targetId: string,
): Promise<string[]> {
  const [fromTests, fromCards] = await Promise.all([
    db()
      .selectDistinct({ id: testQuestions.sourceChunkId })
      .from(testQuestions)
      .innerJoin(tests, eq(tests.id, testQuestions.testId))
      .where(
        and(
          eq(tests.documentId, documentId),
          ne(tests.id, targetId),
          isNotNull(testQuestions.sourceChunkId),
        ),
      ),
    db()
      .selectDistinct({ id: flashcards.sourceChunkId })
      .from(flashcards)
      .innerJoin(flashcardSets, eq(flashcardSets.id, flashcards.setId))
      .where(
        and(
          eq(flashcardSets.documentId, documentId),
          ne(flashcardSets.id, targetId),
          isNotNull(flashcards.sourceChunkId),
        ),
      ),
  ]);
  return [
    ...new Set(
      [...fromTests, ...fromCards]
        .map((row) => row.id)
        .filter((id): id is string => id !== null),
    ),
  ];
}

/**
 * Generates a test or a flashcard set.
 *
 * Runs as a job rather than inside the request because generation is several
 * rate-limited model calls -- a twenty-question test is four or five of them,
 * which is minutes on a free tier. The student watches progress over the
 * WebSocket instead of holding a request open.
 */
export async function generateStudyMaterial(
  job: Job<StudyGenerationJob>,
  publisher: Redis,
): Promise<void> {
  const { kind, targetId, userId, documentId, count, questionTypes, difficulty } =
    studyGenerationJobSchema.parse(job.data);
  const log = logger.child({ jobId: job.id, kind, targetId });
  const table = kind === "test" ? tests : flashcardSets;

  const publish = (
    status: GenerationProgress["status"],
    produced: number,
    message?: string,
  ): Promise<void> =>
    publishGenerationProgress(publisher, {
      targetId,
      kind,
      status,
      produced,
      total: count,
      ...(message ? { message } : {}),
    });

  const markFailed = async (message: string): Promise<void> => {
    await db()
      .update(table)
      .set({
        status: "failed",
        errorMessage: message.slice(0, 1000),
        updatedAt: new Date(),
      })
      .where(eq(table.id, targetId));
    await publish("failed", 0, message);
  };

  log.info("generation started");

  try {
    await db()
      .update(table)
      .set({ status: "generating", updatedAt: new Date() })
      .where(eq(table.id, targetId));
    await publish("generating", 0);

    const avoidChunkIds = await chunksAlreadyUsed(documentId, targetId);
    // Traces are filed under the test or set id, so "what happened when this
    // quiz was made" is one lookup by an id the app already has.
    const trace = { correlationId: targetId };

    if (kind === "test") {
      const result = await generateTest(
        {
          userId,
          documentIds: [documentId],
          questionCount: count,
          types: questionTypes,
          difficulty,
          avoidChunkIds,
        },
        trace,
      );

      // Replace rather than append: a retried job must not double the paper.
      await db().delete(testQuestions).where(eq(testQuestions.testId, targetId));

      await db()
        .insert(testQuestions)
        .values(
          result.questions.map((q, index) => ({
            testId: targetId,
            position: index,
            type: q.type,
            prompt: q.prompt,
            options: q.options ?? null,
            correctAnswer: q.correct_answer,
            explanation: q.explanation,
            sourceChunkId: q.source_chunk_id,
          })),
        );

      await db()
        .update(tests)
        .set({
          status: "ready",
          questionCount: result.questions.length,
          errorMessage: null,
          // Merged in under its own key: the settings the student chose --
          // difficulty, question types -- are part of the test's record and
          // must survive it being generated.
          config: sql`coalesce(${tests.config}, '{}'::jsonb) || ${JSON.stringify({
            generation: { requested: count, ...result.stats },
          })}::jsonb`,
          updatedAt: new Date(),
        })
        .where(eq(tests.id, targetId));

      await publish("ready", result.questions.length);
      log.info({ questions: result.questions.length, stats: result.stats }, "test ready");
      return;
    }

    const result = await generateFlashcards(
      { userId, documentIds: [documentId], cardCount: count, avoidChunkIds },
      trace,
    );

    await db().delete(flashcards).where(eq(flashcards.setId, targetId));
    await db()
      .insert(flashcards)
      .values(
        result.cards.map((card, index) => ({
          setId: targetId,
          position: index,
          front: card.front,
          back: card.back,
          sourceChunkId: card.source_chunk_id,
        })),
      );

    await db()
      .update(flashcardSets)
      .set({
        status: "ready",
        cardCount: result.cards.length,
        errorMessage: null,
        updatedAt: new Date(),
      })
      .where(eq(flashcardSets.id, targetId));

    await publish("ready", result.cards.length);
    log.info({ cards: result.cards.length, stats: result.stats }, "flashcards ready");
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const permanent = err instanceof RagPermanentError;
    const finalAttempt = job.attemptsMade + 1 >= (job.opts.attempts ?? 1);

    log.error({ err: message, permanent, finalAttempt }, "generation failed");

    // Only surface failure once no retry remains, so a transient rate limit
    // does not show the student a permanent error mid-way.
    if (permanent || finalAttempt) {
      await markFailed(message);
    }
    if (permanent) {
      job.discard();
    }
    throw err;
  }
}
