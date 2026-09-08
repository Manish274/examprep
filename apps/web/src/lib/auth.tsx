"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  getSession,
  login as loginRequest,
  logout as logoutRequest,
  onSessionChange,
  register as registerRequest,
  setSession,
  type Session,
} from "./api";

/**
 * Who is signed in.
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
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setLocal] = useState<Session | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    // Read after mount, not during render: localStorage does not exist on the
    // server, and reading it during render makes the markup differ between
    // server and client.
    setLocal(getSession());
    setLoaded(true);
    return onSessionChange(setLocal);
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    setSession(await loginRequest(email, password));
  }, []);

  const signUp = useCallback(async (email: string, password: string) => {
    setSession(await registerRequest(email, password));
  }, []);

  const signOut = useCallback(async () => {
    await logoutRequest();
  }, []);

  const value = useMemo<AuthValue>(
    () => ({ session, loaded, signIn, signUp, signOut }),
    [session, loaded, signIn, signUp, signOut],
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
