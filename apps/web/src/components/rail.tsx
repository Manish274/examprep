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
 * The fixed 256px left rail: the only fixed element in the layout.
 *
 * Icon header row, a new-chat action, the three destinations, then the chat
 * history and an account row pinned to the bottom -- the information
 * architecture from the reference screenshot, drawn in Examprep's own
 * surfaces rather than borrowing anything from it.
 */

export function Rail({
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
      className="ep-scroll"
      style={{
        width: "var(--rail-width)",
        flex: "0 0 var(--rail-width)",
        height: "100svh",
        position: "sticky",
        top: 0,
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-7)",
        padding: "var(--space-6) var(--space-4)",
        background: "var(--bg-rail)",
        borderRight: "1px solid var(--line-1)",
        overflowY: "auto",
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
          <IconButton icon={PanelLeft} label="Collapse rail" size={28} />
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <NavItem icon={Plus} onClick={onNewChat}>
          New
        </NavItem>
      </div>

      {/* The three sections. Also switchable from the composer, so a student
          never has to come back here to change mode. */}
      <RailSection title="Study">
        <NavItem
          icon={MessageCircle}
          active={mode === "chat"}
          onClick={() => onModeChange("chat")}
        >
          Chat
        </NavItem>
        <NavItem
          icon={Target}
          active={mode === "quiz"}
          onClick={() => onModeChange("quiz")}
        >
          Quiz
        </NavItem>
        <NavItem
          icon={Layers}
          active={mode === "cards"}
          onClick={() => onModeChange("cards")}
        >
          Flashcards
        </NavItem>
      </RailSection>

      <RailSection
        title="Chats"
        action={Plus}
        actionLabel="New chat"
        onAction={onNewChat}
        style={{ flex: 1, minHeight: 0 }}
      >
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
              title={session.title ?? "New chat"}
            >
              {session.title ?? "New chat"}
            </NavItem>
          ))
        )}
      </RailSection>

      <div style={{ borderTop: "1px solid var(--line-1)", paddingTop: "var(--space-4)" }}>
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
    </aside>
  );
}
