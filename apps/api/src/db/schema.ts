import {
  boolean,
  index,
  integer,
  jsonb,
  pgEnum,
  pgTable,
  real,
  text,
  timestamp,
  uniqueIndex,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";
import { relations, sql } from "drizzle-orm";

// ─────────────────────────────────────────────────────────────
// Enums
// ─────────────────────────────────────────────────────────────

export const documentStatusEnum = pgEnum("document_status", [
  "queued",
  "parsing",
  "chunking",
  "embedding",
  "indexing",
  "ready",
  "failed",
]);

export const documentKindEnum = pgEnum("document_kind", ["pdf", "pptx", "ppt"]);

export const messageRoleEnum = pgEnum("message_role", [
  "user",
  "assistant",
  "system",
]);

export const explanationModeEnum = pgEnum("explanation_mode", [
  "simple",
  "detailed",
  "exam",
]);

export const questionTypeEnum = pgEnum("question_type", [
  "mcq",
  "short_answer",
  "true_false",
]);

export const generationStatusEnum = pgEnum("generation_status", [
  "pending",
  "generating",
  "ready",
  "failed",
]);

export const attemptStatusEnum = pgEnum("attempt_status", [
  "in_progress",
  "submitted",
  "graded",
]);

// ─────────────────────────────────────────────────────────────
// Identity
// ─────────────────────────────────────────────────────────────

export const users = pgTable(
  "users",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    email: varchar("email", { length: 320 }).notNull(),
    passwordHash: text("password_hash").notNull(),
    displayName: varchar("display_name", { length: 120 }),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [uniqueIndex("users_email_unique").on(sql`lower(${t.email})`)],
);

/**
 * Refresh tokens are persisted so they can be revoked. Only the hash is stored
 * — a database leak must not hand out live sessions.
 */
export const refreshTokens = pgTable(
  "refresh_tokens",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    tokenHash: text("token_hash").notNull(),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
    revokedAt: timestamp("revoked_at", { withTimezone: true }),
    userAgent: text("user_agent"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    uniqueIndex("refresh_tokens_hash_unique").on(t.tokenHash),
    index("refresh_tokens_user_idx").on(t.userId),
  ],
);

// ─────────────────────────────────────────────────────────────
// Documents and chunks
// ─────────────────────────────────────────────────────────────

export const documents = pgTable(
  "documents",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    filename: text("filename").notNull(),
    kind: documentKindEnum("kind").notNull(),
    /** Opaque key resolved by the active StorageProvider. */
    storageKey: text("storage_key").notNull(),
    byteSize: integer("byte_size").notNull(),
    /** sha256 of the file, so re-uploads can be detected. */
    contentHash: varchar("content_hash", { length: 64 }).notNull(),
    status: documentStatusEnum("status").notNull().default("queued"),
    pageCount: integer("page_count"),
    chunkCount: integer("chunk_count"),
    errorMessage: text("error_message"),
    /** Parser used, embedding model, dimensions — needed to detect staleness. */
    processingMeta: jsonb("processing_meta").$type<Record<string, unknown>>(),
    processedAt: timestamp("processed_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    index("documents_user_idx").on(t.userId),
    index("documents_status_idx").on(t.status),
    uniqueIndex("documents_user_hash_unique").on(t.userId, t.contentHash),
  ],
);

/**
 * Chunk text and metadata live in Postgres as well as Qdrant. Qdrant owns the
 * vectors; Postgres owns the truth. That lets us re-index into a different
 * vector store or embedding model without re-parsing the source document, and
 * gives the eval harness a stable corpus to label against.
 */
export const documentChunks = pgTable(
  "document_chunks",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    documentId: uuid("document_id")
      .notNull()
      .references(() => documents.id, { onDelete: "cascade" }),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    chunkIndex: integer("chunk_index").notNull(),
    text: text("text").notNull(),
    tokenCount: integer("token_count").notNull(),
    contentHash: varchar("content_hash", { length: 64 }).notNull(),

    // ── preserved metadata, surfaced as citations ──
    pageNumber: integer("page_number"),
    slideNumber: integer("slide_number"),
    section: text("section"),
    heading: text("heading"),
    headingPath: jsonb("heading_path").$type<string[]>().notNull().default([]),
    charStart: integer("char_start"),
    charEnd: integer("char_end"),

    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    uniqueIndex("chunks_document_index_unique").on(t.documentId, t.chunkIndex),
    index("chunks_document_idx").on(t.documentId),
    index("chunks_user_idx").on(t.userId),
  ],
);

/**
 * Embeddings cached by (model, content hash). Re-ingesting an unchanged
 * document, or re-running the eval harness, then costs zero API calls — which
 * matters a great deal on a free tier.
 */
export const embeddingCache = pgTable(
  "embedding_cache",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    modelId: varchar("model_id", { length: 128 }).notNull(),
    contentHash: varchar("content_hash", { length: 64 }).notNull(),
    dimensions: integer("dimensions").notNull(),
    vector: jsonb("vector").$type<number[]>().notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    uniqueIndex("embedding_cache_model_hash_unique").on(
      t.modelId,
      t.contentHash,
    ),
  ],
);

// ─────────────────────────────────────────────────────────────
// Chat
// ─────────────────────────────────────────────────────────────

export const chatSessions = pgTable(
  "chat_sessions",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    /** Null means the session spans every ready document the user owns. */
    documentId: uuid("document_id").references(() => documents.id, {
      onDelete: "cascade",
    }),
    title: text("title"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [index("chat_sessions_user_idx").on(t.userId)],
);

export const messages = pgTable(
  "messages",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    sessionId: uuid("session_id")
      .notNull()
      .references(() => chatSessions.id, { onDelete: "cascade" }),
    role: messageRoleEnum("role").notNull(),
    content: text("content").notNull(),
    mode: explanationModeEnum("mode"),
    /** True when the model reported the material could not support an answer. */
    unsupported: boolean("unsupported").notNull().default(false),
    /** Query actually issued to retrieval after history-aware rewriting. */
    rewrittenQuery: text("rewritten_query"),
    latencyMs: integer("latency_ms"),
    promptTokens: integer("prompt_tokens"),
    completionTokens: integer("completion_tokens"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [index("messages_session_idx").on(t.sessionId, t.createdAt)],
);

/** Resolved citations for an assistant message, in marker order. */
export const messageSources = pgTable(
  "message_sources",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    messageId: uuid("message_id")
      .notNull()
      .references(() => messages.id, { onDelete: "cascade" }),
    chunkId: uuid("chunk_id")
      .notNull()
      .references(() => documentChunks.id, { onDelete: "cascade" }),
    /** The literal marker used in the answer text, e.g. "S1". */
    marker: varchar("marker", { length: 8 }).notNull(),
    rank: integer("rank").notNull(),
    score: real("score"),
  },
  (t) => [index("message_sources_message_idx").on(t.messageId, t.rank)],
);

// ─────────────────────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────────────────────

export const tests = pgTable(
  "tests",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    documentId: uuid("document_id")
      .notNull()
      .references(() => documents.id, { onDelete: "cascade" }),
    title: text("title").notNull(),
    status: generationStatusEnum("status").notNull().default("pending"),
    questionCount: integer("question_count").notNull().default(0),
    errorMessage: text("error_message"),
    /** Requested mix, difficulty and topic scope. */
    config: jsonb("config").$type<Record<string, unknown>>(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [index("tests_user_idx").on(t.userId)],
);

export const testQuestions = pgTable(
  "test_questions",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    testId: uuid("test_id")
      .notNull()
      .references(() => tests.id, { onDelete: "cascade" }),
    position: integer("position").notNull(),
    type: questionTypeEnum("type").notNull(),
    prompt: text("prompt").notNull(),
    /** MCQ choices; null for short answer. */
    options: jsonb("options").$type<string[]>(),
    /** Index into options for MCQ, or the model answer for short answer. */
    correctAnswer: text("correct_answer").notNull(),
    explanation: text("explanation").notNull(),
    /** Every generated question is traceable to the chunk it came from. */
    sourceChunkId: uuid("source_chunk_id").references(() => documentChunks.id, {
      onDelete: "set null",
    }),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    uniqueIndex("test_questions_position_unique").on(t.testId, t.position),
    index("test_questions_test_idx").on(t.testId),
  ],
);

export const testAttempts = pgTable(
  "test_attempts",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    testId: uuid("test_id")
      .notNull()
      .references(() => tests.id, { onDelete: "cascade" }),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    status: attemptStatusEnum("status").notNull().default("in_progress"),
    score: real("score"),
    maxScore: real("max_score"),
    startedAt: timestamp("started_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    submittedAt: timestamp("submitted_at", { withTimezone: true }),
    gradedAt: timestamp("graded_at", { withTimezone: true }),
  },
  (t) => [index("test_attempts_user_idx").on(t.userId, t.startedAt)],
);

export const attemptAnswers = pgTable(
  "attempt_answers",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    attemptId: uuid("attempt_id")
      .notNull()
      .references(() => testAttempts.id, { onDelete: "cascade" }),
    questionId: uuid("question_id")
      .notNull()
      .references(() => testQuestions.id, { onDelete: "cascade" }),
    response: text("response"),
    isCorrect: boolean("is_correct"),
    /** Partial credit for short answers, 0..1. */
    awarded: real("awarded"),
    /** Grounded explanation of why the answer was right or wrong. */
    feedback: text("feedback"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    uniqueIndex("attempt_answers_unique").on(t.attemptId, t.questionId),
    index("attempt_answers_attempt_idx").on(t.attemptId),
  ],
);

// ─────────────────────────────────────────────────────────────
// Flashcards
// ─────────────────────────────────────────────────────────────

export const flashcardSets = pgTable(
  "flashcard_sets",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    documentId: uuid("document_id")
      .notNull()
      .references(() => documents.id, { onDelete: "cascade" }),
    title: text("title").notNull(),
    status: generationStatusEnum("status").notNull().default("pending"),
    cardCount: integer("card_count").notNull().default(0),
    errorMessage: text("error_message"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [index("flashcard_sets_user_idx").on(t.userId)],
);

export const flashcards = pgTable(
  "flashcards",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    setId: uuid("set_id")
      .notNull()
      .references(() => flashcardSets.id, { onDelete: "cascade" }),
    position: integer("position").notNull(),
    front: text("front").notNull(),
    back: text("back").notNull(),
    sourceChunkId: uuid("source_chunk_id").references(() => documentChunks.id, {
      onDelete: "set null",
    }),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    uniqueIndex("flashcards_position_unique").on(t.setId, t.position),
    index("flashcards_set_idx").on(t.setId),
  ],
);

// ─────────────────────────────────────────────────────────────
// Observability
// ─────────────────────────────────────────────────────────────

/**
 * Local trace sink. One row per pipeline step, holding retrieval scores, fusion
 * ranks, rerank deltas, tokens and latency. Langfuse becomes a second Tracer
 * implementation writing the same payload.
 */
export const traces = pgTable(
  "traces",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id").references(() => users.id, {
      onDelete: "set null",
    }),
    /** "chat" | "test_generation" | "flashcard_generation" | "eval". */
    kind: varchar("kind", { length: 64 }).notNull(),
    /** Links every span of one logical operation. */
    correlationId: uuid("correlation_id").notNull(),
    name: varchar("name", { length: 128 }).notNull(),
    input: jsonb("input").$type<Record<string, unknown>>(),
    output: jsonb("output").$type<Record<string, unknown>>(),
    metadata: jsonb("metadata").$type<Record<string, unknown>>(),
    durationMs: integer("duration_ms"),
    error: text("error"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    index("traces_correlation_idx").on(t.correlationId),
    index("traces_kind_created_idx").on(t.kind, t.createdAt),
  ],
);

// ─────────────────────────────────────────────────────────────
// Relations
// ─────────────────────────────────────────────────────────────

export const usersRelations = relations(users, ({ many }) => ({
  documents: many(documents),
  chatSessions: many(chatSessions),
  tests: many(tests),
  flashcardSets: many(flashcardSets),
  refreshTokens: many(refreshTokens),
}));

export const documentsRelations = relations(documents, ({ one, many }) => ({
  user: one(users, { fields: [documents.userId], references: [users.id] }),
  chunks: many(documentChunks),
}));

export const documentChunksRelations = relations(documentChunks, ({ one }) => ({
  document: one(documents, {
    fields: [documentChunks.documentId],
    references: [documents.id],
  }),
}));

export const chatSessionsRelations = relations(
  chatSessions,
  ({ one, many }) => ({
    user: one(users, { fields: [chatSessions.userId], references: [users.id] }),
    document: one(documents, {
      fields: [chatSessions.documentId],
      references: [documents.id],
    }),
    messages: many(messages),
  }),
);

export const messagesRelations = relations(messages, ({ one, many }) => ({
  session: one(chatSessions, {
    fields: [messages.sessionId],
    references: [chatSessions.id],
  }),
  sources: many(messageSources),
}));

export const testsRelations = relations(tests, ({ one, many }) => ({
  document: one(documents, {
    fields: [tests.documentId],
    references: [documents.id],
  }),
  questions: many(testQuestions),
  attempts: many(testAttempts),
}));

export const testAttemptsRelations = relations(
  testAttempts,
  ({ one, many }) => ({
    test: one(tests, { fields: [testAttempts.testId], references: [tests.id] }),
    answers: many(attemptAnswers),
  }),
);

export const flashcardSetsRelations = relations(
  flashcardSets,
  ({ one, many }) => ({
    document: one(documents, {
      fields: [flashcardSets.documentId],
      references: [documents.id],
    }),
    cards: many(flashcards),
  }),
);
