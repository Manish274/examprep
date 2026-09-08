"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
 * from the upload as soon as the bytes are stored -- parsing a deck takes
 * minutes under free-tier rate limits -- so the percentage here is the worker
 * reporting on itself, and it is the only honest thing to show.
 */

export interface DocumentState extends DocumentRow {
  /** 0-100 while indexing, from the worker. Undefined once ready. */
  progress?: number;
  stage?: string;
}

export function useDocuments() {
  const { send, ready } = useSocket();
  const [documents, setDocuments] = useState<DocumentState[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const watched = useRef(new Set<string>());

  const refresh = useCallback(async () => {
    try {
      const { documents: rows } = await listDocuments();
      setDocuments((previous) => {
        const progressById = new Map(
          previous.map((d) => [d.id, { progress: d.progress, stage: d.stage }]),
        );
        return rows.map((row) => ({
          ...row,
          // Keep any in-flight percentage: the list endpoint knows the status
          // but not how far through the worker is.
          ...(row.status === "ready" || row.status === "failed"
            ? {}
            : (progressById.get(row.id) ?? {})),
        }));
      });
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your material");
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

  // Subscribe to anything still working. Re-run when the socket reconnects,
  // since subscriptions live on the connection and not on the server.
  useEffect(() => {
    if (!ready) {
      watched.current.clear();
      return;
    }
    for (const document of documents) {
      if (document.status === "ready" || document.status === "failed") continue;
      if (watched.current.has(document.id)) continue;
      watched.current.add(document.id);
      send({ type: "subscribe:document", documentId: document.id });
    }
  }, [documents, ready, send]);

  useSocketEvent((event) => {
    if (event.type !== "document:progress") return;
    const { documentId, progress } = event;

    setDocuments((rows) =>
      rows.map((row) =>
        row.id === documentId
          ? { ...row, progress: progress.percent, stage: progress.stage }
          : row,
      ),
    );

    // The terminal stage carries no chunk counts, so the row is re-read rather
    // than patched from the event.
    if (progress.stage === "completed" || progress.stage === "failed") {
      watched.current.delete(documentId);
      void refresh();
    }
  });

  const upload = useCallback(
    async (file: File) => {
      setError(null);
      try {
        const { documentId } = await uploadDocument(file);
        // Shown immediately at 0%: the real row arrives on the next refresh,
        // and a file that vanishes for a second after being chosen reads as a
        // failure.
        setDocuments((rows) => [
          {
            id: documentId,
            filename: file.name,
            kind: file.name.split(".").pop() ?? "pdf",
            byteSize: file.size,
            status: "pending",
            pageCount: null,
            chunkCount: null,
            errorMessage: null,
            createdAt: new Date().toISOString(),
            progress: 0,
            stage: "queued",
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
    working: documents.filter(
      (d) => d.status !== "ready" && d.status !== "failed",
    ),
    loading,
    error,
    upload,
    remove,
    refresh,
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
