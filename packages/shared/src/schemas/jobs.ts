import { z } from "zod";
import { uuidSchema } from "./common.js";
import { documentKindSchema } from "./documents.js";

/** Queue names. Kept here so producer and consumer cannot drift. */
export const QUEUE_DOCUMENT_PROCESSING = "document-processing" as const;

export const documentProcessingJobSchema = z.object({
  documentId: uuidSchema,
  userId: uuidSchema,
  storageKey: z.string(),
  filename: z.string(),
  kind: documentKindSchema,
});
export type DocumentProcessingJob = z.infer<typeof documentProcessingJobSchema>;

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
