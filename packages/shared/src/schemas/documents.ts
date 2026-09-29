import { z } from "zod";

/** Mirrors the `document_status` enum in the database. */
export const documentStatusSchema = z.enum([
  "queued",
  "parsing",
  "chunking",
  "embedding",
  "indexing",
  "ready",
  "failed",
]);
export type DocumentStatus = z.infer<typeof documentStatusSchema>;

export const documentKindSchema = z.enum(["pdf", "pptx", "ppt"]);
export type DocumentKind = z.infer<typeof documentKindSchema>;
