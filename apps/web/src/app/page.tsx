"use client";

// A client component because it is built from the design system's own
// primitives, and Lucide icon components are passed to them as props --
// functions cannot cross the server/client boundary. Next still prerenders
// this to static HTML at build time.
import Link from "next/link";
import { ArrowRight, ArrowUp } from "lucide-react";
import { Badge, Button, Card, Display, Wordmark } from "@/ds";
import { RevealOnScroll } from "@/components/landing-motion";
import { SignedInRedirect } from "./signed-in-redirect";

/**
 * The marketing page, implementing `Examprep Website.dc.html` from the design
 * project: hero over the aurora wash and hairline rings, the two-modes pair,
 * a three-step explainer, the traceability section, a closing card and footer.
 *
 * Prerendered to static HTML at build time. At runtime it sends a student who
 * is already in straight through to the workspace, and reveals each section as
 * it is scrolled to.
 */

const CAPS: React.CSSProperties = {
  fontSize: "var(--caps-size)",
  fontWeight: 500,
  letterSpacing: "var(--caps-track)",
  textTransform: "uppercase",
  color: "var(--text-faint)",
};

const MONO_META: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  color: "var(--text-faint)",
};

const CITATION_CHIP: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  color: "var(--blue-300)",
  background: "var(--blue-tint-08)",
  border: "1px solid var(--blue-tint-32)",
  borderRadius: "var(--radius-xs)",
  padding: "3px 6px",
};

const STEPS = [
  {
    n: "01",
    title: "Upload inline",
    body: "Drop PDFs, slides and photographed notes straight into the composer. Indexing shows a number, not a spinner.",
    meta: "PDF · 2.4 MB · indexing 62%",
  },
  {
    n: "02",
    title: "Ask anything",
    body: "Work through the material in your own words. Answers stay inside what you uploaded and cite where they landed.",
    meta: "4 sources · confidence 0.91",
  },
  {
    n: "03",
    title: "Let it quiz you",
    body: "Switch modes and the same index turns into questions. Weak spots decide the next round.",
    meta: "10 questions · 2 weak spots",
  },
] as const;

const SOURCE_TAGS = [
  "Bio Ch. 4 · p.112",
  "Syllabus 2026 · §3.2",
  "Lecture 09 · slide 7",
  "Tutorial notes · 14 Mar",
] as const;

export default function LandingPage() {
  return (
    <div
      style={{
        background: "var(--bg-page)",
        color: "var(--text-body)",
        fontFamily: "var(--font-sans)",
        overflowX: "hidden",
      }}
    >
      <SignedInRedirect />
      <RevealOnScroll />

      <header
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          zIndex: 5,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "var(--space-6)",
          padding: "22px clamp(20px, 4vw, 48px)",
        }}
      >
        <Wordmark />
        {/* Hidden below 860px, as in the source design. A media query rather
            than the resize listener the canvas used. */}
        <nav
          className="ep-landing-nav"
          style={{ gap: "var(--space-8)", fontSize: "var(--text-sm)" }}
        >
          <a href="#modes" style={{ color: "var(--text-body)" }}>
            Two modes
          </a>
          <a href="#how" style={{ color: "var(--text-body)" }}>
            How it works
          </a>
          <a href="#sources" style={{ color: "var(--text-body)" }}>
            Sources
          </a>
        </nav>
      </header>

      {/* ── hero ─────────────────────────────────────────── */}
      <section
        style={{
          position: "relative",
          minHeight: "100svh",
          display: "grid",
          placeItems: "center",
          padding: "120px clamp(20px, 4vw, 48px) 64px",
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
          aria-hidden="true"
          style={{
            position: "absolute",
            right: -220,
            bottom: -260,
            width: 760,
            height: 760,
            pointerEvents: "none",
          }}
        >
          <div
            style={{ position: "absolute", inset: 0, border: "1px solid var(--line-1)", borderRadius: 999 }}
          />
          <div
            style={{ position: "absolute", inset: 90, border: "1px solid var(--line-2)", borderRadius: 999 }}
          />
          <div
            style={{ position: "absolute", inset: 190, border: "1px solid var(--line-3)", borderRadius: 999 }}
          />
          <div
            style={{ position: "absolute", inset: 300, border: "1px solid var(--line-3)", borderRadius: 999 }}
          />
        </div>

        <div
          className="ep-rise"
          style={{
            position: "relative",
            width: "100%",
            maxWidth: 900,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "var(--space-7)",
            textAlign: "center",
          }}
        >
          <span style={CAPS}>Retrieval-graded revision</span>
          <Display
            as="h1"
            serif="study what you"
            sans="actually forgot"
            align="center"
            style={{ fontSize: "clamp(40px, 8.5vw, 64px)" }}
          />
          <p
            style={{
              margin: 0,
              maxWidth: 560,
              fontSize: "var(--text-lg)",
              lineHeight: "var(--text-lg-lh)",
              color: "var(--text-body)",
              textWrap: "pretty",
            }}
          >
            Upload your notes once. Ask them anything, then let Examprep quiz you
            on the parts you keep missing.
          </p>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              justifyContent: "center",
              gap: "var(--space-4)",
              marginTop: "var(--space-2)",
            }}
          >
            <Link href="/start">
              <Button variant="paper" size="lg" caps>
                Get started
              </Button>
            </Link>
            <a href="#how">
              <Button variant="outline" size="lg" iconEnd={ArrowRight}>
                See how it works
              </Button>
            </a>
          </div>
        </div>

        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            bottom: 0,
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "center",
            gap: "clamp(20px, 5vw, 64px)",
            padding: "20px clamp(20px, 4vw, 48px)",
            borderTop: "1px solid var(--line-1)",
            ...CAPS,
          }}
        >
          <span>Chat with your notes</span>
          <span>Generated quizzes</span>
          <span>Weak-spot tracking</span>
        </div>
      </section>

      {/* ── two modes ────────────────────────────────────── */}
      <section
        data-reveal
        id="modes"
        style={{ padding: "clamp(72px, 10vw, 132px) clamp(20px, 4vw, 48px)" }}
      >
        <div
          style={{
            maxWidth: 1120,
            margin: "0 auto",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-10)",
          }}
        >
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "flex-end",
              justifyContent: "space-between",
              gap: "var(--space-7)",
            }}
          >
            <Display
              serif="one uploaded folder,"
              sans="two ways to work it"
              style={{ fontSize: "clamp(30px, 4.4vw, 44px)" }}
            />
            <p
              style={{
                margin: 0,
                maxWidth: 340,
                fontSize: "var(--text-md)",
                lineHeight: "var(--text-md-lh)",
                color: "var(--text-muted)",
                textWrap: "pretty",
              }}
            >
              Chat and quiz read the same index, so a question you miss points
              back to the page it came from.
            </p>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))",
              gap: "var(--space-7)",
            }}
          >
            <Card className="ep-lift" padding="clamp(24px, 3vw, 36px)">
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
                <div style={{ display: "flex" }}>
                  <Badge tone="accent">Chat</Badge>
                </div>
                <Display
                  as="h3"
                  size="sm"
                  serif="ask your own notes,"
                  sans="read the source"
                />
                <p
                  style={{
                    margin: 0,
                    maxWidth: 420,
                    fontSize: "var(--text-md)",
                    lineHeight: "var(--text-md-lh)",
                    color: "var(--text-body)",
                    textWrap: "pretty",
                  }}
                >
                  Every answer names the chunk it came from, and says when your
                  lecture notes and the textbook disagree.
                </p>
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "var(--space-4)",
                    padding: "var(--space-6)",
                    background: "var(--surface-raised)",
                    border: "1px solid var(--line-1)",
                    borderRadius: "var(--radius-md)",
                  }}
                >
                  <span
                    style={{
                      fontSize: "var(--text-sm)",
                      lineHeight: "var(--text-sm-lh)",
                      color: "var(--paper-0)",
                    }}
                  >
                    Your lecture notes and the textbook disagree on the proton
                    count per ATP. The exam syllabus follows the textbook.
                  </span>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-3)" }}>
                    <span style={CITATION_CHIP}>Bio Ch. 4 · p.112</span>
                    <span style={CITATION_CHIP}>Lecture 09 · slide 7</span>
                  </div>
                </div>
              </div>
            </Card>

            <Card className="ep-lift" padding="clamp(24px, 3vw, 36px)">
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
                <div style={{ display: "flex" }}>
                  <Badge tone="neutral">Quiz · Cards</Badge>
                </div>
                <Display as="h3" size="sm" serif="get tested on" sans="what slipped" />
                <p
                  style={{
                    margin: 0,
                    maxWidth: 420,
                    fontSize: "var(--text-md)",
                    lineHeight: "var(--text-md-lh)",
                    color: "var(--text-body)",
                    textWrap: "pretty",
                  }}
                >
                  Questions and flashcards are generated from the same material,
                  then weighted toward the topics you keep missing.
                </p>
                <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
                  {[
                    {
                      label: "Chemiosmotic coupling",
                      meta: "3 correct",
                      bg: "rgba(127,179,163,.14)",
                      border: "rgba(127,179,163,.32)",
                      colour: "var(--state-correct)",
                      text: "var(--paper-0)",
                    },
                    {
                      label: "Electron transport order",
                      meta: "1 missed",
                      bg: "rgba(232,197,71,.14)",
                      border: "rgba(232,197,71,.32)",
                      colour: "var(--state-review)",
                      text: "var(--paper-0)",
                    },
                    {
                      label: "Proton gradient maths",
                      meta: "queued",
                      bg: "var(--surface-raised)",
                      border: "var(--line-1)",
                      colour: "var(--text-muted)",
                      text: "var(--paper-1)",
                    },
                  ].map((row) => (
                    <div
                      key={row.label}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: "var(--space-5)",
                        padding: "12px 14px",
                        background: row.bg,
                        border: `1px solid ${row.border}`,
                        borderRadius: "var(--radius-md)",
                      }}
                    >
                      <span style={{ fontSize: "var(--text-sm)", color: row.text }}>
                        {row.label}
                      </span>
                      <span
                        style={{
                          fontFamily: "var(--font-mono)",
                          fontSize: 10,
                          color: row.colour,
                        }}
                      >
                        {row.meta}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </Card>
          </div>
        </div>
      </section>

      {/* ── how it works ─────────────────────────────────── */}
      <section
        data-reveal
        id="how"
        style={{ padding: "0 clamp(20px, 4vw, 48px) clamp(72px, 10vw, 132px)" }}
      >
        <div style={{ maxWidth: 1120, margin: "0 auto", borderTop: "1px solid var(--line-1)" }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))",
            }}
          >
            {STEPS.map((step, index) => (
              <div
                key={step.n}
                className="ep-lift"
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: "var(--space-5)",
                  padding: "clamp(32px, 4vw, 52px) clamp(0px, 3vw, 36px)",
                  borderLeft: index === 0 ? undefined : "1px solid var(--line-1)",
                }}
              >
                <span
                  style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--accent)" }}
                >
                  {step.n}
                </span>
                <h3
                  style={{
                    margin: 0,
                    fontSize: "var(--text-2xl)",
                    lineHeight: "var(--text-2xl-lh)",
                    fontWeight: 400,
                    color: "var(--paper-0)",
                  }}
                >
                  {step.title}
                </h3>
                <p
                  style={{
                    margin: 0,
                    fontSize: "var(--text-md)",
                    lineHeight: "var(--text-md-lh)",
                    color: "var(--text-muted)",
                    textWrap: "pretty",
                  }}
                >
                  {step.body}
                </p>
                <span style={MONO_META}>{step.meta}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── traceability ─────────────────────────────────── */}
      <section
        data-reveal
        id="sources"
        style={{ padding: "0 clamp(20px, 4vw, 48px) clamp(72px, 10vw, 132px)" }}
      >
        <div
          style={{
            maxWidth: 760,
            margin: "0 auto",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "var(--space-8)",
            textAlign: "center",
          }}
        >
          <span style={CAPS}>Traceable by default</span>
          <Display
            serif="when your sources"
            sans="disagree, it says so"
            align="center"
            size="md"
            style={{ fontSize: "clamp(26px, 3.6vw, 34px)" }}
          />
          <p
            style={{
              margin: 0,
              maxWidth: 620,
              fontSize: "var(--text-md)",
              lineHeight: "var(--text-md-lh)",
              color: "var(--text-body)",
              textWrap: "pretty",
            }}
          >
            Examprep never asserts what your material does not support. Answers
            carry the chunk, the page and the file they were drawn from, so you
            can check the claim before you memorise it.
          </p>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              justifyContent: "center",
              gap: "var(--space-3)",
            }}
          >
            {SOURCE_TAGS.map((tag) => (
              <span
                key={tag}
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: "var(--text-muted)",
                  border: "1px solid var(--line-1)",
                  borderRadius: "var(--radius-xs)",
                  padding: "5px 8px",
                }}
              >
                {tag}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* ── closing ──────────────────────────────────────── */}
      <section data-reveal style={{ padding: "0 clamp(20px, 4vw, 48px) clamp(56px, 7vw, 88px)" }}>
        <Card
          padding="clamp(36px, 6vw, 76px)"
          style={{ maxWidth: 1120, margin: "0 auto" }}
        >
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "var(--space-9)",
            }}
          >
            <Display
              serif="start with the one"
              sans="lecture you dread"
              style={{ fontSize: "clamp(30px, 4.4vw, 44px)" }}
            />
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-5)",
                alignItems: "flex-start",
              }}
            >
              <Link href="/start">
                <Button variant="primary" size="lg" icon={ArrowUp}>
                  Upload your notes
                </Button>
              </Link>
              <span style={{ fontSize: "var(--text-sm)", color: "var(--text-muted)" }}>
                No library to set up. The composer is the only entry point.
              </span>
            </div>
          </div>
        </Card>
      </section>

      <footer
        style={{
          borderTop: "1px solid var(--line-1)",
          background: "var(--bg-rail)",
          padding: "var(--space-9) clamp(20px, 4vw, 48px)",
        }}
      >
        <div
          style={{
            maxWidth: 1120,
            margin: "0 auto",
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "var(--space-7)",
          }}
        >
          <Wordmark tone="muted" />
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "clamp(16px, 3vw, 40px)",
              fontSize: "var(--text-sm)",
            }}
          >
            <a href="#modes" style={{ color: "var(--text-muted)" }}>
              Two modes
            </a>
            <a href="#how" style={{ color: "var(--text-muted)" }}>
              How it works
            </a>
            <a href="#sources" style={{ color: "var(--text-muted)" }}>
              Sources
            </a>
          </div>
          <span style={MONO_META}>Dark theme only</span>
        </div>
      </footer>
    </div>
  );
}
