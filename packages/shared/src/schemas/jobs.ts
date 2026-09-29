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

// ── progress, worker → Redis → WebSocket hub → browser ─────

/**
 * Where a document is in processing.
 *
 * `parsing` is the RAG service reading, chunking, embedding and indexing the
 * text. It carries a percentage only while it reads page images -- a scanned
 * document, whose text is its pictures. `indexing` is the worker storing the
 * chunks it got back, and counts them. `completed` means searchable.
 *
 * `figures` comes after `completed`: the document is already in use while
 * the images in it are read and added, and a second `completed` follows.
 */
export const jobProgressSchema = z.object({
  stage: z.enum(["parsing", "indexing", "completed", "figures", "failed"]),
  /** 0-100 through the current stage, when the stage can measure it. */
  percent: z.number().min(0).max(100).optional(),
  message: z.string().optional(),
});
export type JobProgress = z.infer<typeof jobProgressSchema>;

export const documentProgressSchema = z.object({
  documentId: uuidSchema,
  progress: jobProgressSchema,
});
export type DocumentProgress = z.infer<typeof documentProgressSchema>;

export const generationProgressSchema = z.object({
  targetId: uuidSchema,
  kind: z.enum(["test", "flashcards"]),
  status: z.enum(["generating", "ready", "failed"]),
  /** Items produced so far; set once the set is ready. */
  produced: z.number().int().nonnegative(),
  total: z.number().int().nonnegative(),
  message: z.string().optional(),
});
export type GenerationProgress = z.infer<typeof generationProgressSchema>;

/** Redis pub/sub channel carrying a document's processing progress. */
export const documentChannel = (documentId: string): string =>
  `doc:progress:${documentId}`;

/** Redis pub/sub channel carrying a test's or flashcard set's progress. */
export const generationChannel = (targetId: string): string =>
  `gen:progress:${targetId}`;
