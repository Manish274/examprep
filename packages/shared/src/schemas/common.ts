import { z } from "zod";

/** Branded id helpers — all ids are UUIDv4 strings. */
export const uuidSchema = z.string().uuid();

export const paginationSchema = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(20),
  cursor: z.string().optional(),
});
export type Pagination = z.infer<typeof paginationSchema>;

/**
 * Explanation style requested by the student. This changes the *style* of the
 * answer only — never the grounding rules, which are constant across modes.
 */
export const explanationModeSchema = z.enum(["simple", "detailed", "exam"]);
export type ExplanationMode = z.infer<typeof explanationModeSchema>;

/** Retrieval strategies, kept as an enum so the eval harness can iterate them. */
export const retrievalStrategySchema = z.enum([
  "bm25",
  "dense",
  "hybrid",
  "hybrid_rerank",
]);
export type RetrievalStrategy = z.infer<typeof retrievalStrategySchema>;

export const errorResponseSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    details: z.unknown().optional(),
  }),
});
export type ErrorResponse = z.infer<typeof errorResponseSchema>;
