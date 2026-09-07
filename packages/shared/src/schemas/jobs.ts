import { z } from "zod";
import { uuidSchema } from "./common.js";
import { documentKindSchema } from "./documents.js";

/** Queue names. Kept here so producer and consumer cannot drift. */
export const QUEUE_DOCUMENT_PROCESSING = "document-processing" as const;
export const QUEUE_STUDY_GENERATION = "study-generation" as const;

export const documentProcessingJobSchema = z.object({
  documentId: uuidSchema,
  userId: uuidSchema,
  storageKey: z.string(),
  filename: z.string(),
  kind: documentKindSchema,
});
export type DocumentProcessingJob = z.infer<typeof documentProcessingJobSchema>;

/**
 * Generating a test or a flashcard set is several rate-limited model calls, so
 * it runs as a job like document processing rather than inside the request.
 */
export const studyGenerationJobSchema = z.object({
  kind: z.enum(["test", "flashcards"]),
  /** tests.id or flashcard_sets.id, depending on kind. */
  targetId: uuidSchema,
  userId: uuidSchema,
  documentId: uuidSchema,
  count: z.number().int().min(1).max(100),
  questionTypes: z.array(z.enum(["mcq", "short_answer", "true_false"])).optional(),
  difficulty: z.string().optional(),
});
export type StudyGenerationJob = z.infer<typeof studyGenerationJobSchema>;

export const jobProgressSchema = z.object({
  stage: z.enum([
    "parsing",
    "chunking",
    "embedding",
    "indexing",
    "completed",
    "failed",
  ]),
  /** 0-100. */
  percent: z.number().min(0).max(100),
  message: z.string().optional(),
  current: z.number().int().nonnegative().optional(),
  total: z.number().int().nonnegative().optional(),
});
export type JobProgress = z.infer<typeof jobProgressSchema>;
