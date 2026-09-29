"use client";

import { Layers, LogOut, MessageCircle, PanelLeft, Plus, Target } from "lucide-react";
import { IconButton, NavItem, RailSection, Wordmark } from "@/ds";
import type { ChatSessionRow } from "@/lib/api";

/** The three sections of the workspace. The rail is the only place to switch. */
export type StudyMode = "chat" | "quiz" | "cards";

/**
 * The left rail: wordmark, the three destinations, a new-chat action beneath
 * them, the chat history, and the student's name pinned to the bottom.
 *
 * Collapsible. It slides out rather than unmounting, so the transition is one
 * width animation and the scroll position of the history survives being hidden.
 */

export function Rail({
  open,
  onToggle,
  mode,
  onModeChange,
  sessions,
  activeSessionId,
  onOpenSession,
  onNewChat,
  name,
  onSignOut,
  documentCount,
}: {
  open: boolean;
  onToggle: () => void;
  mode: StudyMode;
  onModeChange: (mode: StudyMode) => void;
  sessions: ChatSessionRow[];
  activeSessionId: string | null;
  onOpenSession: (id: string) => void;
  onNewChat: () => void;
  name: string;
  onSignOut: () => void;
  documentCount: number;
}) {
  return (
    <aside
      aria-hidden={!open}
      style={{
        width: open ? "var(--rail-width)" : 0,
        flex: `0 0 ${open ? "var(--rail-width)" : "0px"}`,
        height: "100svh",
        position: "sticky",
        top: 0,
        overflow: "hidden",
        borderRight: open ? "1px solid var(--line-1)" : "1px solid transparent",
        // Translucent over the film rather than a solid column, blurred so
        // the names in it stay readable against whatever is moving behind.
        background: "rgba(16, 16, 18, .72)",
        backdropFilter: "blur(18px)",
        WebkitBackdropFilter: "blur(18px)",
        // 220ms is the system's duration for panels and borders.
        transition:
          "width var(--dur-base) var(--ease-standard), border-color var(--dur-base) var(--ease-standard)",
      }}
    >
      <div
        className="ep-scroll"
        style={{
          // Fixed inner width so the contents translate out rather than
          // reflowing to nothing as the rail closes.
          width: "var(--rail-width)",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-7)",
          padding: "var(--space-6) var(--space-4)",
          overflowY: "auto",
          opacity: open ? 1 : 0,
          transform: open ? "none" : "translateX(-8px)",
          transition:
            "opacity var(--dur-base) var(--ease-standard), transform var(--dur-base) var(--ease-standard)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 6px",
          }}
        >
          <Wordmark />
          <IconButton
            icon={PanelLeft}
            label="Hide sidebar"
            size={28}
            onClick={onToggle}
          />
        </div>

        {/* Switching happens here and nowhere else, which is what lets the
            composer stay a text field and an attach button. */}
        <RailSection title="Study">
          <NavItem
            icon={MessageCircle}
            glow
            active={mode === "chat"}
            onClick={() => onModeChange("chat")}
          >
            Chat
          </NavItem>
          <NavItem
            icon={Target}
            glow
            active={mode === "quiz"}
            onClick={() => onModeChange("quiz")}
          >
            Quiz
          </NavItem>
          <NavItem
            icon={Layers}
            glow
            active={mode === "cards"}
            onClick={() => onModeChange("cards")}
          >
            Flashcards
          </NavItem>
          <NavItem icon={Plus} onClick={onNewChat}>
            New chat
          </NavItem>
        </RailSection>

        <RailSection title="Chats" style={{ flex: 1, minHeight: 0 }}>
          {sessions.length === 0 ? (
            <p
              style={{
                margin: 0,
                padding: "var(--space-3) 10px",
                fontSize: "var(--text-sm)",
                color: "var(--text)",
              }}
            >
              Nothing yet.
            </p>
          ) : (
            sessions.map((session) => (
              <NavItem
                key={session.id}
                dot
                active={session.id === activeSessionId}
                onClick={() => onOpenSession(session.id)}
                title={session.title ?? undefined}
              >
                {session.title}
              </NavItem>
            ))
          )}
        </RailSection>

        <div
          style={{ borderTop: "1px solid var(--line-1)", paddingTop: "var(--space-4)" }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-5)",
              padding: "0 6px 6px",
            }}
          >
            <span
              style={{
                width: 24,
                height: 24,
                flex: "0 0 auto",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                borderRadius: "var(--radius-pill)",
                background: "var(--surface-raised)",
                border: "1px solid var(--border-subtle)",
                fontSize: 11,
                color: "var(--text)",
                textTransform: "uppercase",
              }}
            >
              {name.slice(0, 1)}
            </span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span
                style={{
                  display: "block",
                  fontSize: "var(--text-sm)",
                  color: "var(--text)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {name}
              </span>
              <span
                style={{
                  display: "block",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: "var(--text)",
                }}
              >
                {documentCount} {documentCount === 1 ? "document" : "documents"}
              </span>
            </span>
            <IconButton
              icon={LogOut}
              label="End session (deletes your uploads)"
              size={28}
              onClick={onSignOut}
            />
          </div>
        </div>
      </div>
    </aside>
  );
}
