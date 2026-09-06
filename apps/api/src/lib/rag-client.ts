import { env } from "../env.js";
import { upstreamFailure } from "./errors.js";

/**
 * Thin typed client for the Python RAG service. Every call carries the internal
 * service token and a correlation id so a single student request can be traced
 * across both backends.
 */
export interface RagRequestOptions {
  correlationId?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export async function ragFetch<T>(
  path: string,
  init: RequestInit = {},
  options: RagRequestOptions = {},
): Promise<T> {
  const { RAG_SERVICE_URL, INTERNAL_SERVICE_TOKEN } = env();
  const timeoutMs = options.timeoutMs ?? 30_000;

  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, timeoutMs);
  if (options.signal) {
    options.signal.addEventListener("abort", () => {
      controller.abort();
    });
  }

  try {
    const res = await fetch(new URL(path, RAG_SERVICE_URL), {
      ...init,
      signal: controller.signal,
      headers: {
        "content-type": "application/json",
        "x-internal-token": INTERNAL_SERVICE_TOKEN,
        ...(options.correlationId
          ? { "x-correlation-id": options.correlationId }
          : {}),
        ...init.headers,
      },
    });

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw upstreamFailure(
        `RAG service ${res.status} on ${path}: ${body.slice(0, 500)}`,
      );
    }
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw upstreamFailure(`RAG service timed out after ${timeoutMs}ms`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export interface RagHealth {
  status: "ok" | "degraded";
  qdrant: boolean;
  providers: Record<string, string>;
}

export const ragHealth = (options?: RagRequestOptions): Promise<RagHealth> =>
  ragFetch<RagHealth>("/health", { method: "GET" }, { timeoutMs: 5_000, ...options });
