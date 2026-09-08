"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Lightbulb, Sparkles, Target } from "lucide-react";
import {
  AttachmentTile,
  Composer,
  Display,
  SuggestionChip,
} from "@/ds";
import { Rail } from "@/components/rail";
import { ChatThread } from "@/components/chat-thread";
import { QuizPanel } from "@/components/quiz-panel";
import { CardsPanel } from "@/components/cards-panel";
import { MODE_PLACEHOLDER, STUDY_MODES, type StudyMode } from "@/components/modes";
import { useAuth } from "@/lib/auth";
import { useChat, type ChatMode } from "@/hooks/use-chat";
import { useDocuments, describeDocument } from "@/hooks/use-documents";
import { useStudy } from "@/hooks/use-study";

/**
 * The study workspace: fixed rail, one centred column, one composer.
 *
 * Layout follows the design system's rules exactly -- 256px rail, a 680px
 * canvas column, 24px gutter, content centred while the canvas is empty and
 * left-aligned once there is a conversation to read.
 */

const STARTERS = [
  { icon: Lightbulb, text: "Explain the hardest idea in my notes simply" },
  { icon: Target, text: "What am I most likely to be tested on?" },
  { icon: Sparkles, text: "Summarise what I uploaded" },
] as const;

function greeting(hour: number): string {
  if (hour < 12) return "Morning";
  if (hour < 18) return "Afternoon";
  return "Evening";
}

export default function StudyPage() {
  const router = useRouter();
  const { session, loaded, signOut } = useAuth();

  const [mode, setMode] = useState<StudyMode>("chat");
  const [draft, setDraft] = useState("");
  const [explanation] = useState<ChatMode>("detailed");
  const fileInput = useRef<HTMLInputElement>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const docs = useDocuments();
  const chat = useChat();
  const study = useStudy();

  // Computed during render, not in an effect. The server and the student can be
  // in different time zones, so the two renders legitimately differ -- which is
  // what `suppressHydrationWarning` on the heading below is for. Deferring it
  // to an effect would only trade a warning for a visible flicker.
  const salutation = greeting(new Date().getHours());

  useEffect(() => {
    if (loaded && !session) router.replace("/signin");
  }, [loaded, session, router]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [chat.turns.length, chat.streaming]);

  const name = useMemo(
    () => session?.user.email.split("@")[0] ?? "there",
    [session],
  );

  const empty = mode === "chat" && chat.turns.length === 0;
  const firstReady = docs.ready[0]?.id;

  if (!loaded || !session) return null;

  async function onPickFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) await docs.upload(file);
  }

  function onSubmit() {
    if (mode !== "chat") return;
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    void chat.ask(text, explanation, firstReady);
  }

  return (
    <div style={{ display: "flex", minHeight: "100svh", background: "var(--bg-page)" }}>
      <Rail
        mode={mode}
        onModeChange={setMode}
        sessions={chat.sessions}
        activeSessionId={chat.sessionId}
        onOpenSession={(id) => {
          setMode("chat");
          void chat.open(id);
        }}
        onNewChat={() => {
          setMode("chat");
          chat.startNew();
        }}
        email={session.user.email}
        onSignOut={async () => {
          await signOut();
          router.replace("/");
        }}
        documentCount={docs.documents.length}
      />

      <main
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          flexDirection: "column",
          position: "relative",
        }}
      >
        <input
          ref={fileInput}
          type="file"
          accept=".pdf,.ppt,.pptx"
          onChange={onPickFile}
          style={{ display: "none" }}
        />

        {/* The window bar: absolute, top-right of the canvas. */}
        <div
          style={{
            position: "absolute",
            top: 0,
            right: 0,
            zIndex: 4,
            display: "flex",
            alignItems: "center",
            gap: "var(--space-5)",
            padding: "18px var(--gutter)",
          }}
        >
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: chat.connected ? "var(--text-faint)" : "var(--state-review)",
            }}
          >
            {chat.connected ? "connected" : "reconnecting"}
          </span>
        </div>

        <div
          className="ep-scroll"
          style={{
            flex: 1,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            justifyContent: empty ? "center" : "flex-start",
            padding: `${empty ? "0" : "72px"} var(--gutter) var(--space-8)`,
          }}
        >
          <div
            style={{
              width: "100%",
              maxWidth: "var(--canvas-max)",
              margin: "0 auto",
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-9)",
            }}
          >
            {empty ? (
              <div className="ep-rise" style={{ textAlign: "center" }}>
                <Display
                  as="h1"
                  size="md"
                  align="center"
                  suppressHydrationWarning
                  serif={`${salutation}, ${name}`}
                  sans="what are we studying?"
                />
              </div>
            ) : null}

            {mode === "chat" ? (
              <ChatThread
                turns={chat.turns}
                streaming={chat.streaming}
                error={chat.error}
              />
            ) : null}

            {mode === "quiz" ? (
              <QuizPanel
                state={study.quiz}
                onAnswer={study.answer}
                onSubmit={study.submit}
                onReset={study.resetQuiz}
              />
            ) : null}

            {mode === "cards" ? (
              <CardsPanel state={study.cards} onReset={study.resetCards} />
            ) : null}

            <div ref={bottom} />
          </div>
        </div>

        {/* ── composer dock ─────────────────────────────── */}
        <div
          style={{
            position: "sticky",
            bottom: 0,
            padding: `0 var(--gutter) var(--space-8)`,
            background:
              "linear-gradient(to top, var(--bg-page) 62%, transparent)",
          }}
        >
          <div style={{ width: "100%", maxWidth: "var(--canvas-max)", margin: "0 auto" }}>
            <Composer
              value={draft}
              onChange={setDraft}
              onSubmit={onSubmit}
              onAttach={() => fileInput.current?.click()}
              placeholder={MODE_PLACEHOLDER[mode]}
              mode={mode}
              onModeChange={setMode}
              modes={STUDY_MODES}
              disabled={chat.streaming}
              hideSend={mode !== "chat"}
              attachments={
                docs.documents.length > 0 ? (
                  <>
                    {docs.documents.slice(0, 4).map((document) => (
                      <AttachmentTile
                        key={document.id}
                        name={document.filename}
                        meta={describeDocument(document)}
                        progress={
                          document.status === "ready" ? 100 : (document.progress ?? 0)
                        }
                        failed={document.status === "failed"}
                        onRemove={() => void docs.remove(document.id)}
                      />
                    ))}
                  </>
                ) : null
              }
              body={
                mode === "chat" ? undefined : (
                  <StudySetup
                    mode={mode}
                    documents={docs.ready}
                    busy={
                      mode === "quiz"
                        ? study.quiz.status === "generating"
                        : study.cards.status === "generating"
                    }
                    onGenerate={(documentId, count, difficulty) =>
                      mode === "quiz"
                        ? void study.generateQuiz(documentId, count, difficulty)
                        : void study.generateCards(documentId, count)
                    }
                  />
                )
              }
            />

            {empty ? (
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  justifyContent: "center",
                  gap: "var(--space-4)",
                  marginTop: "var(--space-7)",
                }}
              >
                {STARTERS.map((starter) => (
                  <SuggestionChip
                    key={starter.text}
                    icon={starter.icon}
                    onClick={() => setDraft(starter.text)}
                  >
                    {starter.text}
                  </SuggestionChip>
                ))}
              </div>
            ) : null}

            {docs.documents.length === 0 && !docs.loading ? (
              <p
                style={{
                  margin: "var(--space-6) 0 0",
                  textAlign: "center",
                  fontSize: "var(--text-sm)",
                  color: "var(--text-muted)",
                }}
              >
                Nothing uploaded yet. Use the{" "}
                <span style={{ color: "var(--paper-0)" }}>+</span> to add a PDF or
                slide deck — answers only ever come from your own material.
              </p>
            ) : null}

            {docs.error ? (
              <p
                style={{
                  margin: "var(--space-5) 0 0",
                  textAlign: "center",
                  fontSize: "var(--text-sm)",
                  color: "var(--state-wrong)",
                }}
              >
                {docs.error}
              </p>
            ) : null}
          </div>
        </div>
      </main>
    </div>
  );
}

// ── quiz / cards setup, inside the composer shell ──────────

const SELECT: React.CSSProperties = {
  height: 32,
  padding: "0 10px",
  background: "var(--surface-card)",
  border: "1px solid var(--border-subtle)",
  borderRadius: "var(--radius-md)",
  color: "var(--paper-0)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-sm)",
  outline: "none",
  maxWidth: 220,
};

function StudySetup({
  mode,
  documents,
  busy,
  onGenerate,
}: {
  mode: StudyMode;
  documents: { id: string; filename: string }[];
  busy: boolean;
  onGenerate: (documentId: string, count: number, difficulty: string) => void;
}) {
  const [documentId, setDocumentId] = useState("");
  const [count, setCount] = useState(mode === "quiz" ? 5 : 8);
  const [difficulty, setDifficulty] = useState("mixed");

  const chosen = documentId || documents[0]?.id || "";

  if (documents.length === 0) {
    return (
      <p style={{ margin: "2px 2px 14px", fontSize: "var(--text-md)", color: "var(--text-muted)" }}>
        Add a document with the + first — {mode === "quiz" ? "questions" : "cards"}{" "}
        are generated from your own material, so there is nothing to work from yet.
      </p>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: "var(--space-5)",
        padding: "2px 2px 14px",
      }}
    >
      <select
        value={chosen}
        onChange={(e) => setDocumentId(e.target.value)}
        style={SELECT}
        aria-label="Document"
      >
        {documents.map((document) => (
          <option key={document.id} value={document.id}>
            {document.filename}
          </option>
        ))}
      </select>

      <select
        value={count}
        onChange={(e) => setCount(Number(e.target.value))}
        style={{ ...SELECT, maxWidth: 130 }}
        aria-label={mode === "quiz" ? "Questions" : "Cards"}
      >
        {[3, 5, 8, 10, 15].map((n) => (
          <option key={n} value={n}>
            {n} {mode === "quiz" ? "questions" : "cards"}
          </option>
        ))}
      </select>

      {mode === "quiz" ? (
        <select
          value={difficulty}
          onChange={(e) => setDifficulty(e.target.value)}
          style={{ ...SELECT, maxWidth: 120 }}
          aria-label="Difficulty"
        >
          <option value="mixed">Mixed</option>
          <option value="easy">Easy</option>
          <option value="hard">Hard</option>
        </select>
      ) : null}

      <button
        type="button"
        disabled={busy || !chosen}
        onClick={() => onGenerate(chosen, count, difficulty)}
        style={{
          height: 32,
          padding: "0 16px",
          borderRadius: "var(--radius-pill)",
          border: "1px solid var(--accent)",
          background: "var(--accent)",
          color: "#0A0A0B",
          fontFamily: "var(--font-sans)",
          fontSize: "var(--text-sm)",
          fontWeight: 500,
          letterSpacing: "var(--track-wide)",
          cursor: busy ? "not-allowed" : "pointer",
          opacity: busy ? 0.38 : 1,
          transition: "background var(--dur-fast) var(--ease-standard)",
        }}
      >
        {busy ? "Generating" : mode === "quiz" ? "Quiz me" : "Make cards"}
      </button>
    </div>
  );
}
