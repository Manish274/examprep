import { z } from "zod";
import { explanationModeSchema, uuidSchema } from "../schemas/common.js";
import {
  documentProgressSchema,
  generationProgressSchema,
} from "../schemas/jobs.js";

/**
 * WebSocket protocol between browser and the Node API.
 *
 * Every frame is a discriminated union on `type`, so both ends can exhaustively
 * switch and the compiler catches an unhandled case.
 */

/** A citation as surfaced to the browser, keyed by its [S1] marker. */
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

// ── client → server ─────────────────────────────────────────
export const clientEventSchema = z.discriminatedUnion("type", [
  z.object({
    // Sent as the first frame. A token in the URL would land in access logs
    // and referrer headers; an auth frame keeps it in the body.
    type: z.literal("auth"),
    token: z.string().min(1),
  }),
  z.object({
    type: z.literal("subscribe:document"),
    documentId: uuidSchema,
  }),
  z.object({
    type: z.literal("unsubscribe:document"),
    documentId: uuidSchema,
  }),
  z.object({
    type: z.literal("chat:send"),
    sessionId: uuidSchema,
    content: z.string().min(1).max(4000),
    mode: explanationModeSchema.default("detailed"),
  }),
  z.object({ type: z.literal("cancel"), sessionId: uuidSchema }),
  z.object({
    type: z.literal("subscribe:generation"),
    targetId: uuidSchema,
  }),
  z.object({ type: z.literal("ping") }),
]);
export type ClientEvent = z.infer<typeof clientEventSchema>;

// ── server → client ─────────────────────────────────────────
export const serverEventSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("ready"),
    userId: uuidSchema,
  }),
  documentProgressSchema.extend({ type: z.literal("document:progress") }),
  z.object({
    type: z.literal("chat:token"),
    sessionId: uuidSchema,
    messageId: uuidSchema,
    delta: z.string(),
  }),
  z.object({
    type: z.literal("chat:sources"),
    sessionId: uuidSchema,
    messageId: uuidSchema,
    sources: z.array(sourceSchema),
  }),
  generationProgressSchema.extend({ type: z.literal("generation:progress") }),
  z.object({
    type: z.literal("chat:start"),
    sessionId: uuidSchema,
    messageId: uuidSchema,
  }),
  z.object({
    /**
     * Which phase an answer is in before its first token. Most of the wait is
     * here -- rewriting, retrieval, reranking -- with nothing else to show.
     */
    type: z.literal("chat:stage"),
    sessionId: uuidSchema,
    messageId: uuidSchema,
    stage: z.enum(["searching", "writing", "rechecking"]),
    /** Passages the answer is being written from; set with "writing". */
    passages: z.number().int().nonnegative().optional(),
  }),
  z.object({
    type: z.literal("chat:done"),
    sessionId: uuidSchema,
    messageId: uuidSchema,
    /** True when the model could not ground an answer in the material. */
    unsupported: z.boolean().default(false),
    /** Chunks retrieved before the answer was written. */
    retrieved: z.number().int().nonnegative().default(0),
  }),
  z.object({
    type: z.literal("error"),
    code: z.string(),
    message: z.string(),
  }),
  z.object({ type: z.literal("pong") }),
]);
export type ServerEvent = z.infer<typeof serverEventSchema>;
