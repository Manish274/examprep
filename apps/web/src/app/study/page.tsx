"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Lightbulb, PanelLeft, Sparkles, Target } from "lucide-react";
import {
  AttachmentTile,
  Composer,
  Display,
  IconButton,
  SuggestionChip,
} from "@/ds";
import type { ExplanationMode } from "@examprep/shared";
import { Backdrop } from "@/components/backdrop";
import { Rail, type StudyMode } from "@/components/rail";
import { ChatThread } from "@/components/chat-thread";
import { QuizPanel } from "@/components/quiz-panel";
import { CardsPanel } from "@/components/cards-panel";
import { StudySetup } from "@/components/study-setup";
import { useAuth } from "@/lib/auth";
import { useChat } from "@/hooks/use-chat";
import {
  describeActivity,
  describeDocument,
  useDocuments,
} from "@/hooks/use-documents";
import { useStudy } from "@/hooks/use-study";

/**
 * The study workspace: a collapsible rail and one centred column.
 *
 * The canvas has two states, and the composer moves between them. With nothing
 * to read it sits directly under the greeting as one centred unit with the
 * starter chips; once there is a conversation it docks at the bottom and the
 * content scrolls above it. That is the design system's own rule -- centred in
 * the empty state, left-aligned once a conversation exists -- applied to the
 * input as well as the text.
 *
 * Mode switching lives in the rail only. The composer is a text field and an
 * attach button, and nothing else.
 */

const STARTERS = [
  { icon: Lightbulb, text: "Explain the hardest idea in my notes simply" },
  { icon: Target, text: "What am I most likely to be tested on?" },
  { icon: Sparkles, text: "Summarise what I uploaded" },
] as const;

/**
 * How answers are written. The service also knows "simple" and "exam"; the
 * workspace has no control for them yet, so every question asks for this.
 */
const EXPLANATION: ExplanationMode = "detailed";

function greeting(hour: number): string {
  if (hour < 12) return "Morning";
  if (hour < 18) return "Afternoon";
  return "Evening";
}

export default function StudyPage() {
  const router = useRouter();
  const { session, loaded, signOut } = useAuth();

  const [railOpen, setRailOpen] = useState(true);
  const [mode, setMode] = useState<StudyMode>("chat");
  const [draft, setDraft] = useState("");
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
    if (loaded && !session) router.replace("/start");
  }, [loaded, session, router]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [chat.turns.length, chat.streaming]);

  const name = useMemo(
    // First word only: "Morning, Priya" rather than the whole of what was typed.
    () => session?.user.name.split(" ")[0] || "there",
    [session],
  );

  const studyStatus = mode === "quiz" ? study.quiz.status : study.cards.status;

  // Centred while there is nothing to read: an empty chat, a study mode
  // waiting to be set up, or one that is generating and has only a ring to
  // show. Everything else scrolls from the top.
  const centred =
    mode === "chat"
      ? chat.turns.length === 0
      : studyStatus === "idle" || studyStatus === "generating";

  /**
   * The composer is the input, and there is nothing to input while a quiz is
   * being generated or answered. Docking it there covered the paper with a
   * panel whose only control starts a different quiz.
   */
  const showComposer =
    mode === "chat" || studyStatus === "idle" || studyStatus === "failed";

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
    void chat.ask(text, EXPLANATION);
  }

  const composer = (
    <Composer
      value={draft}
      onChange={setDraft}
      onSubmit={onSubmit}
      onAttach={() => fileInput.current?.click()}
      disabled={chat.streaming}
      attachments={
        docs.documents.length > 0
          ? docs.documents.map((document) => (
              <AttachmentTile
                key={document.id}
                name={document.filename}
                meta={describeDocument(document)}
                status={
                  document.status === "ready" || document.status === "failed"
                    ? document.status
                    : "working"
                }
                activity={describeActivity(document)}
                onRemove={() => void docs.remove(document.id)}
              />
            ))
          : null
      }
      body={
        mode === "chat" ? undefined : (
          <StudySetup
            mode={mode}
            documents={docs.ready}
            busy={studyStatus === "generating"}
            onGenerate={(documentId, count, difficulty) =>
              mode === "quiz"
                ? void study.generateQuiz(documentId, count, difficulty)
                : void study.generateCards(documentId, count)
            }
          />
        )
      }
    />
  );

  const hints = (
    <>
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
          <span style={{ color: "var(--paper-0)" }}>+</span> to add a PDF or slide
          deck — answers only ever come from your own material.
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
    </>
  );

  return (
    // No page fill: the film behind it is the background, and painting over
    // it here would leave the workspace the only screen without one.
    <div style={{ display: "flex", minHeight: "100svh", position: "relative" }}>
      <Backdrop />
      <Rail
        open={railOpen}
        onToggle={() => setRailOpen((was) => !was)}
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
        name={session.user.name}
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

        {/* The only way back once the rail is closed. */}
        {!railOpen ? (
          <div
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              zIndex: 4,
              padding: "16px var(--space-7)",
            }}
          >
            <IconButton
              icon={PanelLeft}
              label="Show sidebar"
              size={30}
              onClick={() => setRailOpen(true)}
            />
          </div>
        ) : null}

        <div
          style={{
            position: "absolute",
            top: 0,
            right: 0,
            zIndex: 4,
            display: "flex",
            alignItems: "center",
            gap: "var(--space-5)",
            padding: "22px var(--gutter)",
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

        {centred ? (
          /* One centred unit: greeting, composer, chips. */
          <div
            style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              alignItems: "center",
              padding: "var(--space-13) var(--gutter) var(--space-11)",
            }}
          >
            <div
              className="ep-rise"
              style={{
                width: "100%",
                maxWidth: "var(--canvas-max)",
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-9)",
              }}
            >
              <Display
                as="h1"
                size="md"
                align="center"
                suppressHydrationWarning
                serif={`${salutation}, ${name}`}
                sans="what are we studying?"
              />

              {showComposer ? composer : null}

              {mode === "chat" ? (
                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    justifyContent: "center",
                    gap: "var(--space-4)",
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

              {mode === "quiz" ? (
                <QuizPanel
                  state={study.quiz}
                  onAnswer={study.answer}
                  onSubmit={study.submit}
                  onReset={study.resetQuiz}
                />
              ) : null}

              {mode === "cards" ? (
                <CardsPanel
                  key={study.cards.setId ?? "new"}
                  state={study.cards}
                  onReset={study.resetCards}
                />
              ) : null}

              {showComposer ? hints : null}
            </div>
          </div>
        ) : (
          <>
            <div
              className="ep-scroll"
              style={{
                flex: 1,
                overflowY: "auto",
                // Deeper bottom padding when nothing is docked below, so the
                // last question does not sit flush against the viewport edge.
                padding: `72px var(--gutter) ${showComposer ? "var(--space-8)" : "var(--space-13)"}`,
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
                {mode === "chat" ? (
                  <ChatThread turns={chat.turns} error={chat.error} />
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
                  <CardsPanel
                    key={study.cards.setId ?? "new"}
                    state={study.cards}
                    onReset={study.resetCards}
                  />
                ) : null}

                <div ref={bottom} />
              </div>
            </div>

            {showComposer ? (
              <div
                style={{
                  position: "sticky",
                  bottom: 0,
                  padding: "0 var(--gutter) var(--space-8)",
                  // Fades the scrolling conversation out behind the composer
                  // without hiding the film: opaque ink would read as a bar.
                  background:
                    "linear-gradient(to top, rgba(10,10,11,.92) 55%, transparent)",
                }}
              >
                <div
                  style={{
                    width: "100%",
                    maxWidth: "var(--canvas-max)",
                    margin: "0 auto",
                  }}
                >
                  {composer}
                  {hints}
                </div>
              </div>
            ) : null}
          </>
        )}
      </main>
    </div>
  );
}
