"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createSession,
  deleteSession,
  listSessions,
  loadMessages,
  type ChatSessionRow,
  type MessageSource,
} from "@/lib/api";
import { useSocket, useSocketEvent } from "@/lib/socket";

/**
 * A conversation, live.
 *
 * Messages are persisted by the API as they stream, so this holds only what is
 * needed to render: the turns loaded for the open session plus whatever is
 * arriving right now. Reloading the page rebuilds the same view from
 * `/messages`, citations included.
 */

export interface Turn {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources: MessageSource[];
  unsupported: boolean;
  /** True while tokens are still arriving. */
  streaming?: boolean;
  /** What the answer is doing before its first token. */
  stage?: ChatStage;
  /** Passages it is being written from, once known. */
  passages?: number;
}

export type ChatStage = "searching" | "writing" | "rechecking";

export type ChatMode = "simple" | "detailed" | "exam";

export function useChat() {
  const { send, ready } = useSocket();
  const [sessions, setSessions] = useState<ChatSessionRow[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamingId = useRef<string | null>(null);

  const refreshSessions = useCallback(async () => {
    try {
      const { sessions: rows } = await listSessions();
      // Only conversations that were actually started. The API names a session
      // from its opening question, so an untitled row is one that was created
      // and then abandoned -- a rail full of "New chat" entries nobody wrote.
      setSessions(rows.filter((row) => row.title));
      return rows;
    } catch {
      return [];
    }
  }, []);

  useEffect(() => {
    // The state updates happen after an await inside refreshSessions, so they are
    // not synchronous -- but the rule cannot see through the async boundary.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refreshSessions();
  }, [refreshSessions]);

  const open = useCallback(async (id: string) => {
    setSessionId(id);
    setError(null);
    try {
      const { messages } = await loadMessages(id);
      setTurns(
        messages.map((m) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          sources: m.sources,
          unsupported: m.unsupported,
        })),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open that chat");
    }
  }, []);

  const startNew = useCallback(() => {
    setSessionId(null);
    setTurns([]);
    setError(null);
  }, []);

  const removeSession = useCallback(
    async (id: string) => {
      try {
        await deleteSession(id);
        setSessions((rows) => rows.filter((row) => row.id !== id));
        if (sessionId === id) startNew();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not delete that chat");
      }
    },
    [sessionId, startNew],
  );

  const ask = useCallback(
    async (content: string, mode: ChatMode, documentId?: string) => {
      if (!content.trim() || streaming) return;
      setError(null);

      let id = sessionId;
      if (!id) {
        try {
          const { session } = await createSession(documentId);
          id = session.id;
          setSessionId(id);
          // Not added to the list here: it has no title until the question is
          // persisted, and an untitled row is exactly what the rail filters
          // out. `chat:start` refreshes it a moment later, named.

        } catch (err) {
          setError(err instanceof Error ? err.message : "Could not start a chat");
          return;
        }
      }

      // Shown before the server confirms: a question that disappears for a
      // second after Enter reads as a dropped message.
      setTurns((rows) => [
        ...rows,
        {
          id: `local-${Date.now()}`,
          role: "user",
          content,
          sources: [],
          unsupported: false,
        },
      ]);
      setStreaming(true);

      try {
        send({ type: "chat:send", sessionId: id, content, mode });
      } catch (err) {
        setStreaming(false);
        setError(err instanceof Error ? err.message : "Not connected");
      }
    },
    [send, sessionId, streaming],
  );

  const cancel = useCallback(() => {
    if (sessionId && streaming) send({ type: "cancel", sessionId });
  }, [send, sessionId, streaming]);

  useSocketEvent((event) => {
    switch (event.type) {
      case "chat:start": {
        streamingId.current = event.messageId;
        // The question has been persisted by now, so the session has its name.
        void refreshSessions();
        setTurns((rows) => [
          ...rows,
          {
            id: event.messageId,
            role: "assistant",
            content: "",
            sources: [],
            unsupported: false,
            streaming: true,
            stage: "searching",
          },
        ]);
        break;
      }

      case "chat:stage": {
        setTurns((rows) =>
          rows.map((row) =>
            row.id === event.messageId
              ? {
                  ...row,
                  stage: event.stage,
                  passages: event.passages ?? row.passages,
                }
              : row,
          ),
        );
        break;
      }

      case "chat:token": {
        setTurns((rows) =>
          rows.map((row) =>
            row.id === event.messageId
              ? { ...row, content: row.content + event.delta }
              : row,
          ),
        );
        break;
      }

      case "chat:sources": {
        setTurns((rows) =>
          rows.map((row) =>
            row.id === event.messageId
              ? { ...row, sources: event.sources as unknown as MessageSource[] }
              : row,
          ),
        );
        break;
      }

      case "chat:done": {
        streamingId.current = null;
        setStreaming(false);
        setTurns((rows) =>
          rows.map((row) =>
            row.id === event.messageId
              ? { ...row, streaming: false, unsupported: event.unsupported }
              : row,
          ),
        );
        // The first question names the session, so the rail is stale until
        // this point.
        void refreshSessions();
        break;
      }

      case "error": {
        setStreaming(false);
        setError(event.message);
        // Drop the empty assistant bubble: the API deletes the row too, so
        // leaving it would show a turn that no longer exists on reload.
        setTurns((rows) =>
          rows.filter((row) => !(row.streaming && row.content === "")),
        );
        break;
      }
    }
  });

  return {
    sessions,
    sessionId,
    turns,
    streaming,
    error,
    connected: ready,
    ask,
    cancel,
    open,
    startNew,
    removeSession,
  };
}
