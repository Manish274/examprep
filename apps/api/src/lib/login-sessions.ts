import { and, eq, gt, inArray, isNotNull, isNull, lt, or, sql } from "drizzle-orm";
import { documents, loginSessions, refreshTokens, users } from "@examprep/db";
import { db } from "./db.js";
import { removeDocument } from "./document-removal.js";
import { logger } from "./logger.js";
import { storage } from "./storage.js";

/**
 * The lifetime of a visit, and of everything made during it.
 *
 * A student types a name and starts; that is a visit (a login_sessions row,
 * named "sign-in" throughout the code). Its uploads, chats, quizzes and
 * flashcards belong to it. When it ends -- by leaving, or by its refresh
 * tokens lapsing unused -- all of it is removed: the stored files, the
 * vectors, the rows, and the visitor. The next start begins from nothing.
 *
 * Removal is not left to the database alone. Rows cascade from the session,
 * but the file on disk and the vectors in Qdrant do not, and vectors left
 * behind would keep answering questions about material the student no longer
 * has.
 */

/**
 * A session younger than this is never swept for lack of a live token.
 *
 * Login writes the session and then its first token, and a sweep landing
 * between the two would otherwise delete a sign-in as it begins.
 */
const SWEEP_GRACE_MS = 5 * 60 * 1000;

export async function startLoginSession(userId: string): Promise<string> {
  const [created] = await db()
    .insert(loginSessions)
    .values({ userId })
    .returning({ id: loginSessions.id });
  if (!created) {
    throw new Error("Failed to start a session");
  }
  return created.id;
}

/** Whether a sign-in may still be used: it exists and was not signed out. */
export async function isLoginSessionLive(id: string): Promise<boolean> {
  const [row] = await db()
    .select({ id: loginSessions.id })
    .from(loginSessions)
    .where(and(eq(loginSessions.id, id), isNull(loginSessions.endedAt)))
    .limit(1);
  return Boolean(row);
}

/** Deletes one sign-in and everything that belongs to it. */
async function purgeLoginSession(id: string): Promise<void> {
  const owned = await db()
    .select({
      id: documents.id,
      userId: documents.userId,
      storageKey: documents.storageKey,
    })
    .from(documents)
    .where(eq(documents.loginSessionId, id));

  // One at a time, so a failure part way leaves the remaining rows for the
  // next sweep to find rather than files and vectors nothing points at.
  for (const row of owned) {
    await removeDocument(row);
  }

  // Everything else -- chats, quizzes, flashcards, refresh tokens -- cascades
  // from the session row.
  const [ended] = await db()
    .delete(loginSessions)
    .where(eq(loginSessions.id, id))
    .returning({ userId: loginSessions.userId });

  // A visitor is only a name for the length of one visit, and nothing can
  // sign back in as them once it is over. Their traces cascade with the row,
  // and their upload folder goes with it.
  if (ended) {
    const [gone] = await db()
      .delete(users)
      .where(
        and(
          eq(users.id, ended.userId),
          sql`not exists (select 1 from login_sessions where user_id = ${ended.userId})`,
        ),
      )
      .returning({ id: users.id });
    if (gone) {
      await storage()
        .deleteFolder(gone.id)
        .catch((err: unknown) => {
          logger.warn({ err, userId: gone.id }, "failed to delete upload folder");
        });
    }
  }
  logger.info({ loginSessionId: id, documents: owned.length }, "session cleared");
}

/**
 * Signs out: the session stops working at once, and its material is removed.
 *
 * Marked ended before anything is deleted, so a failure while removing files
 * still leaves a session nobody can use, and the sweep finishes the job.
 */
export async function endLoginSession(id: string): Promise<void> {
  await db()
    .update(loginSessions)
    .set({ endedAt: new Date() })
    .where(eq(loginSessions.id, id));
  await db()
    .update(refreshTokens)
    .set({ revokedAt: new Date() })
    .where(
      and(eq(refreshTokens.loginSessionId, id), isNull(refreshTokens.revokedAt)),
    );
  await purgeLoginSession(id);
}

/**
 * Finds sign-ins that are over and clears them.
 *
 * Over means signed out, or holding no refresh token that could still be
 * used: a student who closes the tab and never returns is gone once their
 * last token lapses, without having to press anything.
 */
async function sweepLoginSessions(now = new Date()): Promise<number> {
  const live = db()
    .select({ id: refreshTokens.id })
    .from(refreshTokens)
    .where(
      and(
        eq(refreshTokens.loginSessionId, loginSessions.id),
        isNull(refreshTokens.revokedAt),
        gt(refreshTokens.expiresAt, now),
      ),
    );

  const over = await db()
    .select({ id: loginSessions.id })
    .from(loginSessions)
    .where(
      or(
        isNotNull(loginSessions.endedAt),
        and(
          lt(loginSessions.createdAt, new Date(now.getTime() - SWEEP_GRACE_MS)),
          sql`not exists (${live})`,
        ),
      ),
    )
    .limit(50);

  for (const { id } of over) {
    try {
      await purgeLoginSession(id);
    } catch (err) {
      // Left in place; the next sweep tries again.
      logger.error({ err, loginSessionId: id }, "failed to clear session");
    }
  }
  return over.length;
}

/** Ids of the ready documents a sign-in owns -- the whole of what it may search. */
export async function sessionDocumentIds(id: string): Promise<string[]> {
  const rows = await db()
    .select({ id: documents.id })
    .from(documents)
    .where(and(eq(documents.loginSessionId, id), eq(documents.status, "ready")));
  return rows.map((r) => r.id);
}

/** Keeps only the ids that belong to this sign-in. */
export async function filterSessionDocuments(
  id: string,
  documentIds: string[],
): Promise<string[]> {
  if (documentIds.length === 0) return [];
  const rows = await db()
    .select({ id: documents.id })
    .from(documents)
    .where(
      and(eq(documents.loginSessionId, id), inArray(documents.id, documentIds)),
    );
  return rows.map((r) => r.id);
}

const SWEEP_EVERY_MS = 10 * 60 * 1000;

/** Runs the sweep now and then on an interval. Returns a stop function. */
export function startSessionSweeper(): () => void {
  const run = () => {
    sweepLoginSessions()
      .then((count) => {
        if (count > 0) logger.info({ count }, "swept ended sessions");
      })
      .catch((err: unknown) => logger.error({ err }, "session sweep failed"));
  };
  run();
  const timer = setInterval(run, SWEEP_EVERY_MS);
  timer.unref();
  return () => clearInterval(timer);
}
