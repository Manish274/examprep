import { z } from "zod";
import { uuidSchema } from "../schemas/common.js";
import { documentStatusSchema } from "../schemas/documents.js";
import { jobProgressSchema } from "../schemas/jobs.js";
import { sourceSchema } from "../schemas/chunks.js";

/**
 * WebSocket protocol between browser and the Node API.
 *
 * Every frame is a discriminated union on `type`, so both ends can exhaustively
 * switch and the compiler catches an unhandled case.
 */

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
    mode: z.enum(["simple", "detailed", "exam"]).default("detailed"),
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
  z.object({
    type: z.literal("document:progress"),
    documentId: uuidSchema,
    progress: jobProgressSchema,
  }),
  z.object({
    type: z.literal("document:status"),
    documentId: uuidSchema,
    status: documentStatusSchema,
    errorMessage: z.string().nullable().optional(),
  }),
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
  z.object({
    type: z.literal("generation:progress"),
    targetId: uuidSchema,
    kind: z.enum(["test", "flashcards"]),
    status: z.enum(["generating", "ready", "failed"]),
    produced: z.number().int().nonnegative().default(0),
    total: z.number().int().nonnegative().default(0),
    message: z.string().optional(),
  }),
  z.object({
    type: z.literal("chat:start"),
    sessionId: uuidSchema,
    messageId: uuidSchema,
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

/** Redis pub/sub channel carrying worker progress back to the API's WS hub. */
export const documentChannel = (documentId: string): string =>
  `doc:progress:${documentId}`;

/** Redis channel carrying generation progress back to the WebSocket hub. */
export const generationChannel = (targetId: string): string =>
  `gen:progress:${targetId}`;
