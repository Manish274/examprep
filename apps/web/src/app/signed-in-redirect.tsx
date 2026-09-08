"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

/**
 * Sends a signed-in student past the marketing page to the workspace.
 *
 * Client-side because the session lives in localStorage, which the server
 * cannot see. Waits for `loaded` so it never redirects on the first paint
 * before the stored session has been read.
 */
export function SignedInRedirect() {
  const { session, loaded } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (loaded && session) router.replace("/study");
  }, [loaded, session, router]);

  return null;
}
