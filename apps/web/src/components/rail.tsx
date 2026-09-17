"use client";

import {
  Layers,
  LogOut,
  MessageCircle,
  PanelLeft,
  Plus,
  Search,
  Target,
} from "lucide-react";
import { IconButton, NavItem, RailSection, Wordmark } from "@/ds";
import type { ChatSessionRow } from "@/lib/api";
import type { StudyMode } from "./modes";

/**
 * The left rail: wordmark, the three destinations, a new-chat action beneath
 * them, the chat history, and an account row pinned to the bottom.
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
  email,
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
  email: string;
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
        background: "var(--bg-rail)",
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
          <div style={{ display: "flex", gap: 2 }}>
            <IconButton icon={Search} label="Search chats" size={28} />
            <IconButton
              icon={PanelLeft}
              label="Hide sidebar"
              size={28}
              onClick={onToggle}
            />
          </div>
        </div>

        {/* The three sections. Switching happens here and nowhere else, which
            is what lets the composer stay a text field and an attach button. */}
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
                color: "var(--text-faint)",
              }}
            >
              Nothing yet.
            </p>
          ) : (
            sessions.map((session) => (
              <NavItem
                key={session.id}
                dot
                muted
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
                color: "var(--paper-1)",
                textTransform: "uppercase",
              }}
            >
              {email.slice(0, 1)}
            </span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span
                style={{
                  display: "block",
                  fontSize: "var(--text-sm)",
                  color: "var(--paper-1)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {email}
              </span>
              <span
                style={{
                  display: "block",
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: "var(--text-faint)",
                }}
              >
                {documentCount} {documentCount === 1 ? "document" : "documents"}
              </span>
            </span>
            <IconButton icon={LogOut} label="Sign out" size={28} onClick={onSignOut} />
          </div>
        </div>
      </div>
    </aside>
  );
}
