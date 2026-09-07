import type { Job } from "bullmq";
import type { Redis } from "ioredis";
import { eq } from "drizzle-orm";
import {
  flashcardSets,
  flashcards,
  testQuestions,
  tests,
} from "@examprep/db";
import { generationChannel, type StudyGenerationJob } from "@examprep/shared";
import { db } from "../lib/db.js";
import { logger } from "../lib/logger.js";
import {
  generateFlashcards,
  generateTest,
  RagPermanentError,
} from "../lib/rag-client.js";

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
  const { kind, targetId, userId, documentId, count } = job.data;
  const log = logger.child({ jobId: job.id, kind, targetId });

  const publish = async (
    status: "generating" | "ready" | "failed",
    produced: number,
    message?: string,
  ): Promise<void> => {
    await publisher.publish(
      generationChannel(targetId),
      JSON.stringify({
        targetId,
        kind,
        status,
        produced,
        total: count,
        ...(message ? { message } : {}),
      }),
    );
  };

  const markFailed = async (message: string): Promise<void> => {
    const table = kind === "test" ? tests : flashcardSets;
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
    const table = kind === "test" ? tests : flashcardSets;
    await db()
      .update(table)
      .set({ status: "generating", updatedAt: new Date() })
      .where(eq(table.id, targetId));
    await publish("generating", 0);

    if (kind === "test") {
      const result = await generateTest(
        {
          userId,
          documentIds: [documentId],
          questionCount: count,
          types: job.data.questionTypes,
          difficulty: job.data.difficulty,
        },
        { correlationId: job.id },
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
          config: { requested: count, ...result.stats },
          updatedAt: new Date(),
        })
        .where(eq(tests.id, targetId));

      await publish("ready", result.questions.length);
      log.info({ questions: result.questions.length, stats: result.stats }, "test ready");
      return;
    }

    const result = await generateFlashcards(
      { userId, documentIds: [documentId], cardCount: count },
      { correlationId: job.id },
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
    const finalAttempt = (job.attemptsMade ?? 0) + 1 >= (job.opts.attempts ?? 1);

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
