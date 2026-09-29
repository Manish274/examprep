/**
 * Where the API lives.
 *
 * The frontend runs on :3000 and the API on :3001, so this is a real
 * cross-origin call rather than a same-origin path. The API only accepts the
 * origin in its WEB_ORIGIN setting; moving the web app without changing that
 * produces a browser error that looks nothing like its cause.
 */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:3001";

/** The WebSocket endpoint, derived so the two can never drift apart. */
export function socketUrl(): string {
  const url = new URL("/ws", API_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
