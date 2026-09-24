"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button, Display, Wordmark, usePrefersReducedMotion } from "@/ds";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";

/**
 * The way in: a name and a button, over a film.
 *
 * There are no accounts. Each start is a new visit with an empty workspace,
 * and ending it deletes what was uploaded -- so there is nothing a password
 * would be protecting between visits.
 *
 * The video is decoration: muted, looping, and behind a dim wash that keeps
 * the field and its label legible. A student who has asked for reduced motion
 * gets its first frame as a still image instead.
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

/** Mirrors the API's limit, so the field stops where the server would. */
const MAX_NAME = 60;

export default function StartPage() {
  const router = useRouter();
  const { session, loaded, start } = useAuth();

  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    const element = video.current;
    if (!element) return;
    // Autoplay is declined by some browsers even when muted, and the setting
    // can change while the page is open, so play and pause are driven here
    // rather than left to the attribute alone.
    if (reduced) {
      element.pause();
      element.currentTime = 0;
    } else {
      // A refused play is not an error worth surfacing: the still frame and
      // the wash behind it are a perfectly good backdrop.
      void element.play().catch(() => undefined);
    }
  }, [reduced]);

  useEffect(() => {
    if (loaded && session) router.replace("/study");
  }, [loaded, session, router]);

  const trimmed = name.trim();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!trimmed) return;
    setError(null);
    setBusy(true);
    try {
      await start(trimmed);
      router.replace("/study");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not reach Examprep. Is the API running?",
      );
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
      <div className="ep-backdrop" aria-hidden="true">
        <video
          ref={video}
          src="/violet-crown.webm"
          muted
          loop
          playsInline
          preload="auto"
          // Chrome only honours autoplay on a muted video; the effect above
          // covers the browsers that still refuse it.
          autoPlay={!reduced}
          tabIndex={-1}
        />
      </div>

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
          serif="start with the one"
          sans="lecture you dread"
        />

        <form
          onSubmit={submit}
          style={{ display: "flex", flexDirection: "column", gap: "var(--space-7)" }}
        >
          <label style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
            <span style={LABEL}>Your name</span>
            <input
              type="text"
              required
              autoFocus
              autoComplete="given-name"
              maxLength={MAX_NAME}
              placeholder="What should we call you?"
              value={name}
              onChange={(e) => setName(e.target.value)}
              style={FIELD}
            />
          </label>

          {error ? (
            <p
              role="alert"
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
            disabled={busy || !trimmed}
          >
            {busy ? "One moment" : "Get started"}
          </Button>
        </form>

        <p
          style={{
            margin: 0,
            fontSize: "var(--text-sm)",
            lineHeight: 1.5,
            color: "var(--text-muted)",
          }}
        >
          No account needed. Each visit starts fresh, and ending it deletes
          anything you uploaded.
        </p>
      </div>
    </main>
  );
}
