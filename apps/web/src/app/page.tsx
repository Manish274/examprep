"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useSocket } from "@/lib/socket";
import { API_URL } from "@/lib/config";

/**
 * Placeholder while the design system is being imported.
 *
 * It exists to prove the new workspace actually stands up: providers mount,
 * the API is reachable cross-origin, and the WebSocket completes its
 * handshake. All three fail silently in different ways, so seeing them
 * separately is worth a screen.
 */
export default function Home() {
  const { session, loaded } = useAuth();
  const { ready } = useSocket();
  const [api, setApi] = useState<"checking" | "up" | "down">("checking");

  useEffect(() => {
    fetch(new URL("/health", API_URL))
      .then((r) => setApi(r.ok ? "up" : "down"))
      .catch(() => setApi("down"));
  }, []);

  return (
    <main className="mx-auto flex min-h-dvh max-w-xl flex-col justify-center gap-6 px-6">
      <h1 className="font-serif text-3xl">ExamPrep</h1>
      <p className="text-muted text-sm">
        Frontend scaffold is up. The interface is waiting on the design system
        import.
      </p>
      <dl className="border-subtle divide-subtle divide-y rounded-[var(--radius-md)] border text-sm">
        {[
          ["API", api === "checking" ? "checking…" : api],
          ["Session", !loaded ? "loading…" : (session?.user.email ?? "signed out")],
          ["WebSocket", session ? (ready ? "connected" : "connecting…") : "idle"],
        ].map(([label, value]) => (
          <div key={label} className="flex justify-between gap-4 px-4 py-2.5">
            <dt className="text-muted">{label}</dt>
            <dd className="font-mono text-xs">{value}</dd>
          </div>
        ))}
      </dl>
    </main>
  );
}
