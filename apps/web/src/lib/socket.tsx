"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { ClientEvent, ServerEvent } from "@examprep/shared";
import { getSession, onSessionChange } from "./api";
import { socketUrl } from "./config";

/**
 * One WebSocket for the whole app.
 *
 * Streaming answers, document processing progress and generation progress all
 * arrive on the same connection. A socket per feature would mean three
 * handshakes, three auth frames and three reconnect loops fighting each other
 * over one browser tab.
 *
 * The protocol types come from @examprep/shared, so the frames this sends and
 * the frames the API accepts cannot drift apart without the compiler saying so.
 */

type Listener = (event: ServerEvent) => void;

interface SocketValue {
  /** Whether the handshake completed. Sending before this is a protocol error. */
  ready: boolean;
  send: (event: ClientEvent) => void;
  subscribe: (listener: Listener) => () => void;
}

const SocketContext = createContext<SocketValue | null>(null);

/** Backoff between reconnection attempts, capped so it stays responsive. */
const RETRY_MS = [500, 1000, 2000, 5000, 10_000] as const;

export function SocketProvider({ children }: { children: ReactNode }) {
  const socket = useRef<WebSocket | null>(null);
  const listeners = useRef(new Set<Listener>());
  const attempt = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closing = useRef(false);
  // Frames sent before the handshake finishes are held rather than dropped:
  // a page that mounts and immediately subscribes to a document would
  // otherwise lose that subscription and show a progress bar that never moves.
  const backlog = useRef<ClientEvent[]>([]);
  const [ready, setReady] = useState(false);

  // `connect` schedules itself on close. Held in a ref so the retry reaches the
  // current function rather than closing over a half-initialised binding.
  const reconnect = useRef<() => void>(() => undefined);

  const connect = useCallback(() => {
    const session = getSession();
    if (!session?.accessToken || closing.current) return;
    if (socket.current && socket.current.readyState <= WebSocket.OPEN) return;

    const ws = new WebSocket(socketUrl());
    socket.current = ws;

    ws.addEventListener("open", () => {
      // The token goes in the first frame, never the URL: a URL lands in
      // access logs and referrer headers.
      ws.send(JSON.stringify({ type: "auth", token: session.accessToken }));
    });

    ws.addEventListener("message", (event) => {
      let frame: ServerEvent;
      try {
        frame = JSON.parse(String(event.data)) as ServerEvent;
      } catch {
        return;
      }

      if (frame.type === "ready") {
        attempt.current = 0;
        setReady(true);
        for (const held of backlog.current.splice(0)) {
          ws.send(JSON.stringify(held));
        }
      }

      for (const listener of listeners.current) listener(frame);
    });

    ws.addEventListener("close", () => {
      setReady(false);
      socket.current = null;
      if (closing.current || !getSession()) return;

      const delay = RETRY_MS[Math.min(attempt.current, RETRY_MS.length - 1)]!;
      attempt.current += 1;
      timer.current = setTimeout(() => reconnect.current(), delay);
    });

    // 'error' is always followed by 'close', which owns the retry. Handling it
    // here too would double every backoff.
    ws.addEventListener("error", () => undefined);
  }, []);

  useEffect(() => {
    reconnect.current = connect;
    closing.current = false;
    connect();

    // Signing in or out changes who the socket is: reconnect as the new user,
    // or close when there is no longer one.
    const stop = onSessionChange((next) => {
      socket.current?.close();
      socket.current = null;
      setReady(false);
      if (next) connect();
    });

    return () => {
      closing.current = true;
      stop();
      if (timer.current) clearTimeout(timer.current);
      socket.current?.close();
      socket.current = null;
    };
  }, [connect]);

  const value = useMemo<SocketValue>(
    () => ({
      ready,
      send: (event) => {
        const ws = socket.current;
        if (ws?.readyState === WebSocket.OPEN && ready) {
          ws.send(JSON.stringify(event));
        } else {
          backlog.current.push(event);
        }
      },
      subscribe: (listener) => {
        listeners.current.add(listener);
        return () => listeners.current.delete(listener);
      },
    }),
    [ready],
  );

  return (
    <SocketContext.Provider value={value}>{children}</SocketContext.Provider>
  );
}

export function useSocket(): SocketValue {
  const value = useContext(SocketContext);
  if (!value) {
    throw new Error("useSocket must be used inside a SocketProvider");
  }
  return value;
}

/** Subscribes to server frames for the lifetime of a component. */
export function useSocketEvent(listener: Listener): void {
  const { subscribe } = useSocket();
  const latest = useRef(listener);

  // Assigned in an effect, not during render: a ref written while rendering is
  // torn by concurrent rendering, and React flags it for exactly that reason.
  useEffect(() => {
    latest.current = listener;
  });

  useEffect(
    // The subscription reads through the ref so a listener defined inline does
    // not resubscribe on every render, tearing down and rebuilding the set.
    () => subscribe((event) => latest.current(event)),
    [subscribe],
  );
}
