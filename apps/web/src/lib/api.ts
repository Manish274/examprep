import type { DocumentStatus, Source } from "@examprep/shared";
import { API_URL } from "./config";

/**
 * The HTTP client every screen goes through.
 *
 * Two things live here rather than in the components that call it, because
 * both are easy to get subtly wrong in one place out of twenty:
 *
 * **Token refresh.** Access tokens are deliberately short-lived. A student
 * reading a long answer and then asking a follow-up would otherwise be signed
 * out mid-thought. A 401 refreshes once and retries; concurrent 401s share one
 * refresh rather than racing each other into revoking the token they just got.
 *
 * **Error shape.** The API answers failures as `{error:{code,message}}`, with
 * the specific reason for a validation failure under `details`. Left to each
 * caller, half of them would surface "[object Object]" or a generic line.
 */

export interface Session {
  accessToken: string;
  refreshToken: string;
  user: { id: string; name: string };
}

const STORAGE_KEY = "examprep.session";

let session: Session | null = null;
let refreshing: Promise<boolean> | null = null;
const listeners = new Set<(session: Session | null) => void>();

export function getSession(): Session | null {
  if (session) return session;
  if (typeof window === "undefined") return null;
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    const parsed = saved ? (JSON.parse(saved) as Session) : null;
    // A session saved by the old email sign-in has no name, and its tokens
    // name no visit the API still accepts. Starting again is the only way on.
    session = typeof parsed?.user?.name === "string" ? parsed : null;
  } catch {
    // A corrupt entry must not stop the app from loading; it only means
    // starting again.
    session = null;
  }
  return session;
}

export function setSession(next: Session | null): void {
  session = next;
  if (typeof window !== "undefined") {
    try {
      if (next) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      else window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // Private mode. The session still works for this tab.
    }
  }
  for (const listener of listeners) listener(next);
}

export function onSessionChange(fn: (session: Session | null) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function refreshAccess(): Promise<boolean> {
  const current = getSession();
  if (!current?.refreshToken) return false;

  // Shared, not per-caller: three requests failing at once must not fire three
  // refreshes. The server rotates the refresh token on use, so the second would
  // present one that the first had already revoked.
  refreshing ??= (async () => {
    try {
      const response = await fetch(new URL("/api/auth/refresh", API_URL), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ refreshToken: current.refreshToken }),
      });
      if (!response.ok) {
        setSession(null);
        return false;
      }
      const tokens = (await response.json()) as {
        accessToken: string;
        refreshToken: string;
      };
      setSession({ ...current, ...tokens });
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();

  return refreshing;
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Skip the bearer token: starting and ending a visit carry none. */
  anonymous?: boolean;
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
  retried = false,
): Promise<T> {
  const { body, anonymous, headers, ...rest } = options;
  const isForm = body instanceof FormData;

  const merged = new Headers(headers);
  if (!anonymous) {
    const token = getSession()?.accessToken;
    if (token) merged.set("authorization", `Bearer ${token}`);
  }
  if (body !== undefined && !isForm) merged.set("content-type", "application/json");

  const response = await fetch(new URL(path, API_URL), {
    ...rest,
    headers: merged,
    ...(body === undefined
      ? {}
      : { body: isForm ? body : JSON.stringify(body) }),
  });

  if (response.status === 401 && !anonymous && !retried) {
    if (await refreshAccess()) return request<T>(path, options, true);
    setSession(null);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    // Not JSON — a proxy error page, or the API being down.
  }

  if (!response.ok) {
    const error = (
      payload as {
        error?: { code?: string; message?: string; details?: unknown };
      } | null
    )?.error;
    throw new ApiError(
      firstDetail(error?.details) ??
        error?.message ??
        (text.slice(0, 300) || response.statusText),
      response.status,
      error?.code ?? "unknown",
    );
  }

  return payload as T;
}

/**
 * The reason a request failed validation, when the API gave one.
 *
 * "Request failed validation" tells a student nothing; "Enter your name" is
 * the line worth showing.
 */
function firstDetail(details: unknown): string | undefined {
  if (!Array.isArray(details)) return undefined;
  const first: unknown = details[0];
  if (first && typeof first === "object" && "message" in first) {
    const { message } = first as { message: unknown };
    if (typeof message === "string" && message) return message;
  }
  return undefined;
}

// ── visits ─────────────────────────────────────────────────

/** Begins a visit under a name. There are no accounts to sign in to. */
export const start = (name: string) =>
  request<Session>("/api/auth/start", {
    method: "POST",
    body: { name },
    anonymous: true,
  });

export async function logout(): Promise<void> {
  const current = getSession();
  setSession(null);
  if (!current?.refreshToken) return;
  try {
    await request("/api/auth/logout", {
      method: "POST",
      body: { refreshToken: current.refreshToken },
      anonymous: true,
    });
  } catch {
    // The local session is already gone; a failed revoke is not worth
    // blocking the sign-out the student asked for.
  }
}

// ── documents ──────────────────────────────────────────────

export interface DocumentRow {
  id: string;
  filename: string;
  kind: string;
  byteSize: number;
  status: DocumentStatus;
  pageCount: number | null;
  chunkCount: number | null;
  /** Images still being read after the document became ready. */
  figuresPending: number | null;
  errorMessage: string | null;
  createdAt: string;
}

export const listDocuments = () =>
  request<{ documents: DocumentRow[] }>("/api/documents?limit=50");

export const uploadDocument = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<{ documentId: string; status: string }>("/api/documents", {
    method: "POST",
    body: form,
  });
};

export const deleteDocument = (id: string) =>
  request<void>(`/api/documents/${id}`, { method: "DELETE" });

// ── chat ───────────────────────────────────────────────────

export interface ChatSessionRow {
  id: string;
  title: string | null;
  documentId: string | null;
  createdAt: string;
  updatedAt: string;
}

export type MessageSource = Source;

interface MessageRow {
  id: string;
  role: "user" | "assistant";
  content: string;
  unsupported: boolean;
  createdAt: string;
  sources: MessageSource[];
}

export const listSessions = () =>
  request<{ sessions: ChatSessionRow[] }>("/api/chat/sessions");

/** A new chat over everything uploaded in this visit. */
export const createSession = () =>
  request<{ session: ChatSessionRow }>("/api/chat/sessions", {
    method: "POST",
    body: {},
  });

export const loadMessages = (sessionId: string) =>
  request<{ messages: MessageRow[] }>(
    `/api/chat/sessions/${sessionId}/messages`,
  );

// ── quizzes ────────────────────────────────────────────────

interface TestRow {
  id: string;
  title: string;
  status: "pending" | "generating" | "ready" | "failed";
  questionCount: number;
  documentId: string;
  errorMessage: string | null;
  createdAt: string;
}

export interface QuestionRow {
  id: string;
  position: number;
  type: "mcq" | "short_answer" | "true_false";
  prompt: string;
  options: string[] | null;
}

export interface GradedResult {
  questionId: string;
  prompt: string;
  type: string;
  options: string[] | null;
  yourAnswer: string | null;
  correctAnswer: string;
  isCorrect: boolean;
  awarded: number;
  feedback: string;
}

export const createTest = (input: {
  documentId: string;
  questionCount: number;
  difficulty: string;
  types?: string[];
}) =>
  request<{ testId: string; title: string }>("/api/tests", {
    method: "POST",
    body: input,
  });

export const loadTest = (id: string) =>
  request<{ test: TestRow; questions: QuestionRow[] }>(`/api/tests/${id}`);

export const startAttempt = (testId: string) =>
  request<{ attempt: { id: string } }>(`/api/tests/${testId}/attempts`, {
    method: "POST",
  });

export const submitAttempt = (
  attemptId: string,
  answers: { questionId: string; response: string | null }[],
) =>
  request<{ score: number; maxScore: number; results: GradedResult[] }>(
    `/api/tests/attempts/${attemptId}/submit`,
    { method: "POST", body: { answers } },
  );

// ── flashcards ─────────────────────────────────────────────

interface FlashcardSetRow {
  id: string;
  title: string;
  status: "pending" | "generating" | "ready" | "failed";
  cardCount: number;
  errorMessage: string | null;
  createdAt: string;
}

export interface FlashcardRow {
  id: string;
  front: string;
  back: string;
  position: number;
}

export const createFlashcardSet = (documentId: string, cardCount: number) =>
  request<{ setId: string; title: string }>("/api/flashcards", {
    method: "POST",
    body: { documentId, cardCount },
  });

export const loadFlashcardSet = (id: string) =>
  request<{ set: FlashcardSetRow; cards: FlashcardRow[] }>(
    `/api/flashcards/${id}`,
  );
