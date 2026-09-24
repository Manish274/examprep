"use client";

import { useEffect, useRef } from "react";
import { usePrefersReducedMotion } from "@/ds";

/**
 * The film behind the app.
 *
 * Fixed behind everything, muted and looping, under a wash and a vignette
 * that keep type readable while the motion stays plainly visible. It belongs
 * to the app itself -- the start screen and the workspace -- and never to the
 * marketing page, which was designed around its own aurora.
 *
 * A student who has asked for reduced motion gets the first frame as a still
 * image: the backdrop is decoration, and decoration is the first thing that
 * should stop moving.
 */
export function Backdrop() {
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
      // the wash over it are a perfectly good backdrop.
      void element.play().catch(() => undefined);
    }
  }, [reduced]);

  return (
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
  );
}
