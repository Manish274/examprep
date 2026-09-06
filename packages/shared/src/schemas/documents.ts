import { z } from "zod";
import { uuidSchema } from "./common.js";

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

export const SUPPORTED_MIME_TYPES: Record<string, DocumentKind> = {
  "application/pdf": "pdf",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation":
    "pptx",
  "application/vnd.ms-powerpoint": "ppt",
};

export const documentSchema = z.object({
  id: uuidSchema,
  userId: uuidSchema,
  filename: z.string(),
  kind: documentKindSchema,
  byteSize: z.number().int().nonnegative(),
  status: documentStatusSchema,
  pageCount: z.number().int().nonnegative().nullable(),
  chunkCount: z.number().int().nonnegative().nullable(),
  errorMessage: z.string().nullable(),
  createdAt: z.string().datetime(),
  updatedAt: z.string().datetime(),
});
export type Document = z.infer<typeof documentSchema>;

/** Response to a successful upload — processing continues asynchronously. */
export const uploadAcceptedSchema = z.object({
  documentId: uuidSchema,
  jobId: z.string(),
  status: documentStatusSchema,
});
export type UploadAccepted = z.infer<typeof uploadAcceptedSchema>;
