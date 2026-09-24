"use client";

import { useEffect } from "react";

/**
 * What makes the marketing page feel awake: its sections arrive as they are
 * scrolled to. Written straight to the DOM rather than held in state, since a
 * section that fades in would otherwise re-render the page on every scroll.
 *
 * Nothing here loads a video -- the film belongs to the rest of the app, and
 * the landing page stays the quiet thing it was designed to be.
 */

/**
 * Fades every `data-reveal` section in the first time it is scrolled to.
 *
 * Observes rather than wraps, so the page's markup stays exactly as it was
 * designed. Each section is unobserved once shown: re-animating on the way
 * back up turns a scroll into a flicker.
 */
export function RevealOnScroll() {
  useEffect(() => {
    const sections = document.querySelectorAll("[data-reveal]");

    // Nothing may stay hidden because the mechanism that shows it is missing.
    if (typeof IntersectionObserver === "undefined") {
      for (const section of sections) section.classList.add("is-in");
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add("is-in");
          observer.unobserve(entry.target);
        }
      },
      // A little before the lower edge, so a section finishes arriving as it
      // comes into view rather than starting once it is already there.
      { rootMargin: "0px 0px -12% 0px", threshold: 0.04 },
    );

    for (const section of sections) observer.observe(section);
    return () => observer.disconnect();
  }, []);

  return null;
}
