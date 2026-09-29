import { useSyncExternalStore } from "react";

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

/**
 * True while the operating system asks for reduced motion.
 *
 * Subscribed rather than read into state, so the first paint already knows the
 * answer and a change of the OS setting reaches every component at once. The
 * server snapshot is `false`: the stylesheet already stops the animations, and
 * this hook only governs the effects that CSS cannot reach.
 */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia(REDUCED_MOTION);
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    },
    () => window.matchMedia(REDUCED_MOTION).matches,
    () => false,
  );
}
