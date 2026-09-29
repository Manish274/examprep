import { eq } from "drizzle-orm";
import { documents } from "@examprep/db";
import { db } from "./db.js";
import { logger } from "./logger.js";
import { documents as documentQueue } from "./queue.js";
import { ragDeleteDocument } from "./rag-client.js";
import { storage } from "./storage.js";

/**
 * Removes one document everywhere it lives: a job still waiting to index it,
 * its vectors, the stored file, and then the row. Chunks, chats scoped to the
 * document and anything generated from it cascade with the row.
 *
 * Outside stores first, the row last. If the vectors cannot be deleted this
 * throws with the row still in place, so the document stays visible and the
 * removal can be retried -- by the student, or by the next session sweep. The
 * other order would leave vectors nothing points at, still answering
 * questions about material that is gone.
 */
export async function removeDocument(row: {
  id: string;
  userId: string;
  storageKey: string;
}): Promise<void> {
  // A job that is already running cannot be removed; the worker checks for
  // the row when it finishes and drops what it indexed.
  await documentQueue()
    .remove(row.id)
    .catch(() => undefined);

  await ragDeleteDocument(row.id, row.userId);

  if (row.storageKey) {
    // A leftover file is harmless to answers and swept with its folder when
    // the session ends, so it does not hold up the rest.
    await storage()
      .delete(row.storageKey)
      .catch((err: unknown) => {
        logger.warn({ err, documentId: row.id }, "failed to delete stored file");
      });
  }

  await db().delete(documents).where(eq(documents.id, row.id));
}
