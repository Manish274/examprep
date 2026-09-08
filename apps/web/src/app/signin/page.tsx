"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Button, Display, Wordmark } from "@/ds";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";

/**
 * Sign in and registration, on one screen.
 *
 * Two surfaces exist in this product and this is the second; there is nothing
 * to see signed out, so the page is the gate rather than a step inside one.
 */

const FIELD: React.CSSProperties = {
  width: "100%",
  height: 44,
  padding: "0 14px",
  background: "var(--surface-raised)",
  border: "1px solid var(--border-subtle)",
  borderRadius: "var(--radius-md)",
  color: "var(--paper-0)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-md)",
  outline: "none",
};

const LABEL: React.CSSProperties = {
  fontSize: "var(--caps-size)",
  fontWeight: 500,
  letterSpacing: "var(--caps-track)",
  textTransform: "uppercase",
  color: "var(--text-faint)",
};

function SignInForm() {
  const params = useSearchParams();
  const router = useRouter();
  const { session, loaded, signIn, signUp } = useAuth();

  const [registering, setRegistering] = useState(params.get("mode") === "register");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (loaded && session) router.replace("/study");
  }, [loaded, session, router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (registering) await signUp(email.trim(), password);
      else await signIn(email.trim(), password);
      router.replace("/study");
    } catch (err) {
      // The API's own message is the useful one -- "Incorrect email or
      // password", "An account with that email already exists" -- so it is
      // shown rather than replaced with something generic.
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not reach Examprep. Is the API running?",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main
      style={{
        minHeight: "100svh",
        display: "grid",
        placeItems: "center",
        padding: "var(--space-10) var(--gutter)",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <div
        aria-hidden="true"
        style={{
          position: "absolute",
          inset: 0,
          background: "var(--wash-aurora)",
          pointerEvents: "none",
        }}
      />

      <div
        className="ep-rise"
        style={{
          position: "relative",
          width: "100%",
          maxWidth: 380,
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-9)",
        }}
      >
        <Link href="/" style={{ alignSelf: "flex-start" }}>
          <Wordmark />
        </Link>

        <Display
          as="h1"
          size="md"
          serif={registering ? "start with the one" : "pick up where"}
          sans={registering ? "lecture you dread" : "you left off"}
        />

        <form
          onSubmit={submit}
          style={{ display: "flex", flexDirection: "column", gap: "var(--space-7)" }}
        >
          <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            <span style={LABEL}>Email</span>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              style={FIELD}
            />
          </label>

          <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            <span style={LABEL}>Password</span>
            <input
              type="password"
              required
              minLength={8}
              autoComplete={registering ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              style={FIELD}
            />
            {registering ? (
              <span style={{ fontSize: "var(--text-xs)", color: "var(--text-muted)" }}>
                At least 8 characters.
              </span>
            ) : null}
          </label>

          {error ? (
            <p
              style={{
                margin: 0,
                fontSize: "var(--text-sm)",
                color: "var(--state-wrong)",
              }}
            >
              {error}
            </p>
          ) : null}

          <Button
            type="submit"
            variant="paper"
            size="lg"
            caps
            fullWidth
            disabled={busy}
          >
            {busy ? "One moment" : registering ? "Create account" : "Sign in"}
          </Button>
        </form>

        <button
          type="button"
          onClick={() => {
            setRegistering((was) => !was);
            setError(null);
          }}
          style={{
            background: "transparent",
            border: "none",
            padding: 0,
            cursor: "pointer",
            textAlign: "left",
            fontFamily: "var(--font-sans)",
            fontSize: "var(--text-sm)",
            color: "var(--text-muted)",
          }}
        >
          {registering
            ? "Already have an account? Sign in"
            : "New here? Create an account"}
        </button>
      </div>
    </main>
  );
}

export default function SignInPage() {
  // useSearchParams needs a suspense boundary for static rendering.
  return (
    <Suspense fallback={null}>
      <SignInForm />
    </Suspense>
  );
}
