"use client";

import { useEffect, useRef } from "react";
import { usePrefersReducedMotion } from "@/ds";

/**
 * What makes the marketing page feel awake.
 *
 * Both pieces are decoration, and both are written straight to the DOM rather
 * than held in state: a light that follows the cursor would otherwise
 * re-render the page on every pointer event, and a section that fades in would
 * re-render it on every scroll.
 *
 * Nothing here loads a video -- the film belongs to the start screen, and the
 * landing page stays the quiet thing it was designed to be.
 */

/**
 * A soft light under the pointer, across whichever section contains it.
 *
 * Absolute inside its parent, so the parent needs `position: relative` -- the
 * hero already does.
 */
export function HeroGlow() {
  const glow = useRef<HTMLDivElement>(null);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    const element = glow.current;
    const section = element?.parentElement;
    if (!element || !section || reduced) return;

    const move = (event: PointerEvent) => {
      const box = section.getBoundingClientRect();
      element.style.setProperty("--mx", `${event.clientX - box.left}px`);
      element.style.setProperty("--my", `${event.clientY - box.top}px`);
      element.classList.add("is-lit");
    };
    const leave = () => element.classList.remove("is-lit");

    section.addEventListener("pointermove", move);
    section.addEventListener("pointerleave", leave);
    return () => {
      section.removeEventListener("pointermove", move);
      section.removeEventListener("pointerleave", leave);
    };
  }, [reduced]);

  return <div ref={glow} className="ep-hero-glow" aria-hidden="true" />;
}

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
