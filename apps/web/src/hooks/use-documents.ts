"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { JobProgress } from "@examprep/shared";
import {
  deleteDocument,
  listDocuments,
  uploadDocument,
  type DocumentRow,
} from "@/lib/api";
import { useSocket, useSocketEvent } from "@/lib/socket";

/**
 * The student's uploaded material, and the progress of anything still indexing.
 *
 * Upload is the only entry point in this product, so this hook is shared by
 * every mode rather than owned by the chat screen: a quiz needs a ready
 * document just as much as a question does.
 *
 * Progress arrives over the WebSocket rather than by polling. The API returns
 * from the upload as soon as the bytes are stored, so what is shown here is
 * the worker reporting on itself: a percentage only once there is one to
 * report. A document is ready once its text is indexed; the images in it are
 * read afterwards, and are shown as on their way while that happens.
 */

export interface DocumentState extends DocumentRow {
  /** Where the worker last said it was. */
  stage?: JobProgress["stage"];
  /** 0-100 through the current stage, when the stage can measure it. */
  progress?: number;
}

/** Whether the worker still has something to say about this document. */
function inProgress(document: DocumentRow): boolean {
  if (document.status === "failed") return false;
  return document.status !== "ready" || (document.figuresPending ?? 0) > 0;
}

/** How long to wait before asking again for a list that failed to load. */
const RETRY_MS = 5000;

export function useDocuments() {
  const { send, ready } = useSocket();
  const [documents, setDocuments] = useState<DocumentState[]>([]);
  const [loading, setLoading] = useState(true);
  // Whether the list has ever loaded. Until it has, an empty list means
  // "unknown", and saying "nothing uploaded yet" over a server that is only
  // restarting reads as the student's work having been lost.
  const [known, setKnown] = useState(false);
  const [failures, setFailures] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const watched = useRef(new Set<string>());

  const refresh = useCallback(async () => {
    try {
      const { documents: rows } = await listDocuments();
      setDocuments((previous) => {
        const before = new Map(previous.map((d) => [d.id, d]));
        return rows.map((row) => {
          const known = before.get(row.id);
          // Keep any in-flight stage: the list endpoint knows the status but
          // not how far through the worker is.
          return inProgress(row) && known
            ? { ...row, stage: known.stage, progress: known.progress }
            : row;
        });
      });
      setKnown(true);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your material");
      setFailures((count) => count + 1);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Same as the chat hook: refresh awaits the network before it sets anything,
    // so this is a fetch on mount rather than a synchronous cascade.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  // A list that never loaded is asked for again until it does, so the page
  // comes back by itself when the server does, rather than sitting on an
  // error until someone thinks to reload.
  useEffect(() => {
    if (known || failures === 0) return;
    const timer = window.setTimeout(() => void refresh(), RETRY_MS);
    return () => window.clearTimeout(timer);
  }, [known, failures, refresh]);

  // Subscribe to anything still working. Re-run when the socket reconnects,
  // since subscriptions live on the connection and not on the server.
  useEffect(() => {
    if (!ready) {
      watched.current.clear();
      return;
    }
    for (const document of documents) {
      if (!inProgress(document)) continue;
      if (watched.current.has(document.id)) continue;
      watched.current.add(document.id);
      send({ type: "subscribe:document", documentId: document.id });
    }
  }, [documents, ready, send]);

  useSocketEvent((event) => {
    if (event.type !== "document:progress") return;
    const { documentId, progress } = event;
    const settled = progress.stage === "completed" || progress.stage === "failed";

    setDocuments((rows) =>
      rows.map((row) =>
        row.id === documentId
          ? settled
            ? { ...row, stage: undefined, progress: undefined }
            : { ...row, stage: progress.stage, progress: progress.percent }
          : row,
      ),
    );

    // A settled stage carries no chunk counts, so the row is re-read rather
    // than patched from the event -- and the re-read row says whether its
    // figures are still to come, which re-subscribes it if they are.
    if (settled) {
      watched.current.delete(documentId);
      void refresh();
    }
  });

  const upload = useCallback(
    async (file: File) => {
      setError(null);
      try {
        const { documentId } = await uploadDocument(file);
        // Shown immediately: the real row arrives on the next refresh, and a
        // file that vanishes for a second after being chosen reads as a
        // failure.
        setDocuments((rows) => [
          {
            id: documentId,
            filename: file.name,
            kind: file.name.split(".").pop() ?? "pdf",
            byteSize: file.size,
            status: "queued",
            pageCount: null,
            chunkCount: null,
            figuresPending: null,
            figuresUnread: null,
            errorMessage: null,
            createdAt: new Date().toISOString(),
          },
          ...rows,
        ]);
        if (ready) send({ type: "subscribe:document", documentId });
        watched.current.add(documentId);
        return documentId;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Upload failed");
        return null;
      }
    },
    [ready, send],
  );

  const remove = useCallback(
    async (id: string) => {
      try {
        await deleteDocument(id);
        setDocuments((rows) => rows.filter((row) => row.id !== id));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not remove that");
      }
    },
    [],
  );

  return {
    documents,
    ready: documents.filter((d) => d.status === "ready"),
    loading,
    known,
    error,
    upload,
    remove,
  };
}

/**
 * What is missing from a ready document, or null when nothing is: images the
 * model could not read after every try. Said on the tile, so a scan with
 * pages missing never passes for a complete one.
 */
export function describeGaps(
  document: DocumentState,
): { short: string; long: string } | null {
  const unread = document.figuresUnread ?? 0;
  if (document.status !== "ready" || unread <= 0) return null;
  const images = `${unread} image${unread === 1 ? "" : "s"}`;
  return {
    short: `${images} unread`,
    long:
      `${images} in this file could not be read — the image model was busy ` +
      "or out of its daily allowance, so what is on them cannot be searched. " +
      "Remove the file and upload it again later to add them.",
  };
}

/** "PDF · 2.4 MB" — the metadata line on an attachment tile. */
export function describeDocument(document: DocumentState): string {
  const size = document.byteSize
    ? `${(document.byteSize / 1_048_576).toFixed(1)} MB`
    : null;
  const pages = document.pageCount ? `${document.pageCount} pages` : null;
  return [document.kind.toUpperCase(), size, pages].filter(Boolean).join(" · ");
}

/**
 * What the worker is doing with a document, in the tile's words, or null when
 * it is doing nothing. A number only when the worker has one.
 */
export function describeActivity(document: DocumentState): string | null {
  const percent = document.progress === undefined ? "" : ` ${document.progress}%`;
  if (document.status === "failed") return null;
  if (document.status !== "ready") {
    if (document.stage === "indexing") return `indexing${percent}`;
    if (document.stage === "parsing" && percent) return `reading pages${percent}`;
    return "processing";
  }
  if ((document.figuresPending ?? 0) > 0) {
    return `reading figures${document.stage === "figures" ? percent : ""}`;
  }
  return null;
}
