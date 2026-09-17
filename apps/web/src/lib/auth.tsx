"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import {
  getSession,
  logout as logoutRequest,
  onSessionChange,
  setSession,
  start as startRequest,
  type Session,
} from "./api";

/**
 * Who is here: the name typed on the start screen, and the visit's tokens.
 *
 * The session lives in module state rather than only in React state, because
 * the HTTP client needs it outside any component -- including inside the
 * refresh it performs when a request comes back 401. This context subscribes
 * to that store rather than owning it, so a refresh that replaces the tokens
 * re-renders the app without the client having to know React exists.
 */

interface AuthValue {
  session: Session | null;
  /** False until the stored session has been read; the first paint is unknown. */
  loaded: boolean;
  /** Starts a new visit under this name. */
  start: (name: string) => Promise<void>;
  /** Ends the visit; its uploads and chats are deleted. */
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  // `useSyncExternalStore` rather than an effect that copies the store into
  // state: the session genuinely is an external store, read by the HTTP client
  // outside React and mutated by a token refresh deep inside a failed request.
  // The server snapshot is null because localStorage does not exist there, and
  // returning it separately is what keeps the server markup and the first
  // client render identical.
  const session = useSyncExternalStore(onSessionChange, getSession, () => null);
  // Server renders false, client renders true, with no effect and no second
  // state variable to keep in step.
  const loaded = useSyncExternalStore(
    onSessionChange,
    () => true,
    () => false,
  );

  const start = useCallback(async (name: string) => {
    setSession(await startRequest(name));
  }, []);

  const signOut = useCallback(async () => {
    await logoutRequest();
  }, []);

  const value = useMemo<AuthValue>(
    () => ({ session, loaded, start, signOut }),
    [session, loaded, start, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used inside an AuthProvider");
  }
  return value;
}
