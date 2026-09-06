import { z } from "zod";
import { uuidSchema } from "./common.js";

/**
 * Metadata preserved on every chunk from ingestion through to the citation
 * rendered in the UI. Page and slide are mutually exclusive in practice but
 * both are nullable so one shape covers PDF and PPTX.
 */
export const chunkMetadataSchema = z.object({
  documentId: uuidSchema,
  documentName: z.string(),
  chunkIndex: z.number().int().nonnegative(),
  pageNumber: z.number().int().positive().nullable(),
  slideNumber: z.number().int().positive().nullable(),
  section: z.string().nullable(),
  heading: z.string().nullable(),
  /** Full ancestry, e.g. ["Unit 3", "Normalization", "Third Normal Form"]. */
  headingPath: z.array(z.string()).default([]),
  charStart: z.number().int().nonnegative().nullable(),
  charEnd: z.number().int().nonnegative().nullable(),
  contentHash: z.string(),
});
export type ChunkMetadata = z.infer<typeof chunkMetadataSchema>;

export const retrievedChunkSchema = chunkMetadataSchema.extend({
  chunkId: uuidSchema,
  text: z.string(),
  /** Final score after fusion and reranking. */
  score: z.number(),
  denseScore: z.number().nullable().default(null),
  sparseScore: z.number().nullable().default(null),
  rrfScore: z.number().nullable().default(null),
  rerankScore: z.number().nullable().default(null),
});
export type RetrievedChunk = z.infer<typeof retrievedChunkSchema>;

/** A citation as surfaced to the frontend, keyed by its [S1] marker. */
export const sourceSchema = z.object({
  marker: z.string(),
  chunkId: uuidSchema,
  documentId: uuidSchema,
  documentName: z.string(),
  pageNumber: z.number().int().positive().nullable(),
  slideNumber: z.number().int().positive().nullable(),
  headingPath: z.array(z.string()).default([]),
  snippet: z.string(),
});
export type Source = z.infer<typeof sourceSchema>;
