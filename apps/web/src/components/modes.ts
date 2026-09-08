import { Layers, MessageCircle, Target } from "lucide-react";
import type { SegmentOption } from "@/ds";

/**
 * The three sections, defined once.
 *
 * They appear twice -- as rail destinations and as the composer's mode switch
 * -- and a student switching in one place must land in the same place as the
 * other, so the list has exactly one definition.
 */
export type StudyMode = "chat" | "quiz" | "cards";

export const STUDY_MODES: readonly SegmentOption<StudyMode>[] = [
  { value: "chat", label: "Chat", icon: MessageCircle },
  { value: "quiz", label: "Quiz", icon: Target },
  { value: "cards", label: "Cards", icon: Layers },
];

export const MODE_PLACEHOLDER: Record<StudyMode, string> = {
  chat: "Ask anything about your notes",
  quiz: "Describe what to be tested on, or just send to cover everything",
  cards: "Describe what to make cards from, or just send to cover everything",
};
