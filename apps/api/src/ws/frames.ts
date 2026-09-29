import type { WSContext } from "hono/ws";
import type { ServerEvent } from "@examprep/shared";
import { logger } from "../lib/logger.js";

/** Writes one frame. A socket that has gone away is not worth an error. */
export function send(socket: WSContext, event: ServerEvent): void {
  try {
    socket.send(JSON.stringify(event));
  } catch (err) {
    logger.debug({ err }, "failed to write to socket");
  }
}

/** Writes an error frame; the connection stays open. */
export function fail(socket: WSContext, code: string, message: string): void {
  send(socket, { type: "error", code, message });
}
