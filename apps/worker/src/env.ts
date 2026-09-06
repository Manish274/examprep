import { z } from "zod";

const envSchema = z.object({
  NODE_ENV: z
    .enum(["development", "test", "production"])
    .default("development"),
  LOG_LEVEL: z
    .enum(["fatal", "error", "warn", "info", "debug", "trace"])
    .default("info"),

  DATABASE_URL: z.string().url(),
  REDIS_URL: z.string().url(),
  RAG_SERVICE_URL: z.string().url(),
  INTERNAL_SERVICE_TOKEN: z.string().min(8),

  STORAGE_LOCAL_PATH: z.string().default("./storage/uploads"),

  /** How many documents this worker processes at once. */
  WORKER_CONCURRENCY: z.coerce.number().int().positive().default(2),

  /**
   * Free-tier quotas are the real constraint on ingestion throughput. The
   * worker throttles itself to stay under them, so a large upload gets slower
   * rather than failing.
   */
  EMBEDDING_MAX_RPM: z.coerce.number().int().positive().default(100),
  LLM_MAX_RPM: z.coerce.number().int().positive().default(15),
});

export type Env = z.infer<typeof envSchema>;

let cached: Env | null = null;

export function loadEnv(source: NodeJS.ProcessEnv = process.env): Env {
  const parsed = envSchema.safeParse(source);
  if (!parsed.success) {
    const issues = parsed.error.issues
      .map((i) => `  - ${i.path.join(".")}: ${i.message}`)
      .join("\n");
    throw new Error(`Invalid worker environment:\n${issues}`);
  }
  return parsed.data;
}

export function env(): Env {
  cached ??= loadEnv();
  return cached;
}
