import {
  boolean,
  index,
  integer,
  jsonb,
  pgEnum,
  pgTable,
  primaryKey,
  real,
  text,
  timestamp,
  uniqueIndex,
  uuid,
  varchar,
} from "drizzle-orm/pg-core";

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

/**
 * Where a chunk's text came from. A vision transcription is a model's
 * reading of a picture, not words lifted from the file; conflating the two
 * would let a mis-transcribed formula become an authoritative citation.
 */
export const contentSourceEnum = pgEnum("content_source", ["text", "vision"]);

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

/**
 * Someone using the app. There are no accounts: a student types a name and
 * starts, and each start is a new user whose material lives only as long as
 * that visit.
 */
export const users = pgTable("users", {
  id: uuid("id").primaryKey().defaultRandom(),
  /** The name typed on the start screen. */
  displayName: varchar("display_name", { length: 120 }),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

/**
 * One sign-in, from login until sign-out or until its refresh tokens lapse.
 *
 * Everything a student makes -- uploads, chats, quizzes, flashcards -- belongs
 * to the sign-in it was made in, and goes when that sign-in ends. Each login
 * starts from nothing: the product keeps no library between visits.
 */
export const loginSessions = pgTable(
  "login_sessions",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    /** Set on sign-out. The row stays until its material has been removed. */
    endedAt: timestamp("ended_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [index("login_sessions_user_idx").on(t.userId)],
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
    /** Rotation keeps this, so every token in a chain names one sign-in. */
    loginSessionId: uuid("login_session_id")
      .notNull()
      .references(() => loginSessions.id, { onDelete: "cascade" }),
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
    loginSessionId: uuid("login_session_id")
      .notNull()
      .references(() => loginSessions.id, { onDelete: "cascade" }),
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
    /**
     * Images still being read after the document became ready. The text is
     * searchable as soon as it is indexed; the figures follow, and this is
     * what tells the web app to show that they are on their way.
     */
    figuresPending: integer("figures_pending"),
    /**
     * Images that could still not be read after every retry -- the model
     * overloaded throughout, or the day's quota spent. Shown on the document,
     * so a scan missing pages never passes for a complete one.
     */
    figuresUnread: integer("figures_unread"),
    processedAt:timestamp("processed_at", { withTimezone: true }),
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
    index("documents_login_session_idx").on(t.loginSessionId),
    // Per sign-in: the same file uploaded again in a later session is a new
    // upload, not a duplicate of something already gone.
    uniqueIndex("documents_session_hash_unique").on(
      t.loginSessionId,
      t.contentHash,
    ),
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
    source: contentSourceEnum("source").notNull().default("text"),

    // ── preserved metadata, surfaced as citations ──
    pageNumber: integer("page_number"),
    // The last page, when the chunk crosses a page break.
    pageEnd: integer("page_end"),
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

/**
 * What a vision model read in an image, by (model and prompt version, image
 * hash). A free-tier vision model allows a few dozen calls a day, so a retried
 * upload or a second copy of the same slides must not pay for them again.
 */
export const visionCache = pgTable(
  "vision_cache",
  {
    modelKey: varchar("model_key", { length: 160 }).notNull(),
    contentHash: varchar("content_hash", { length: 64 }).notNull(),
    /** Null when the model judged the image to carry no study content. */
    reading: text("reading"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [primaryKey({ columns: [t.modelKey, t.contentHash] })],
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
    loginSessionId: uuid("login_session_id")
      .notNull()
      .references(() => loginSessions.id, { onDelete: "cascade" }),
    /** Null means the chat spans every ready document in its sign-in. */
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
  (t) => [
    index("chat_sessions_user_idx").on(t.userId),
    index("chat_sessions_login_session_idx").on(t.loginSessionId),
  ],
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
    loginSessionId: uuid("login_session_id")
      .notNull()
      .references(() => loginSessions.id, { onDelete: "cascade" }),
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
  (t) => [
    index("tests_user_idx").on(t.userId),
    index("tests_login_session_idx").on(t.loginSessionId),
  ],
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
    loginSessionId: uuid("login_session_id")
      .notNull()
      .references(() => loginSessions.id, { onDelete: "cascade" }),
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
  (t) => [
    index("flashcard_sets_user_idx").on(t.userId),
    index("flashcard_sets_login_session_idx").on(t.loginSessionId),
  ],
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
 * ranks, rerank deltas, tokens and latency. Langfuse, when configured, is a
 * second sink receiving the same payload.
 *
 * A trace holds the visitor's questions and excerpts of their uploads, so it
 * goes with the visitor rather than outliving them with the link cut.
 */
export const traces = pgTable(
  "traces",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: uuid("user_id").references(() => users.id, {
      onDelete: "cascade",
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
