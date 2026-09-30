import { z } from "zod";

/** Every id in the system is a UUIDv4 string. */
export const uuidSchema = z.string().uuid();

/**
 * Explanation style requested by the student. This changes the *style* of the
 * answer only -- never the grounding rules, which are constant across modes.
 */
export const explanationModeSchema = z.enum(["simple", "detailed", "exam"]);
export type ExplanationMode = z.infer<typeof explanationModeSchema>;

/**
 * Retrieval strategies the RAG service accepts. The retrieval inspector runs
 * them side by side; the Python side has the same enum, and a contract test
 * keeps the two equal.
 */
export const retrievalStrategySchema = z.enum([
  "bm25",
  "dense",
  "hybrid",
  "hybrid_rerank",
]);
