"use client";

import { useCallback, useRef, useState } from "react";
import {
  createFlashcardSet,
  createTest,
  loadFlashcardSet,
  loadTest,
  startAttempt,
  submitAttempt,
  type FlashcardRow,
  type GradedResult,
  type QuestionRow,
} from "@/lib/api";
import { useSocket, useSocketEvent } from "@/lib/socket";

/**
 * Quiz and flashcard generation.
 *
 * Both are background jobs -- several rate-limited model calls each -- so the
 * request returns a id and the work reports itself over the WebSocket. The two
 * share one hook because they share that lifecycle exactly; only the payload
 * at the end differs.
 */

export type StudyStatus = "idle" | "generating" | "ready" | "failed";

export interface QuizState {
  status: StudyStatus;
  testId: string | null;
  attemptId: string | null;
  title: string;
  questions: QuestionRow[];
  answers: Record<string, string>;
  results: GradedResult[] | null;
  score: number | null;
  maxScore: number | null;
  message: string | null;
  produced: number;
  total: number;
}

export interface CardsState {
  status: StudyStatus;
  setId: string | null;
  title: string;
  cards: FlashcardRow[];
  message: string | null;
  produced: number;
  total: number;
}

const EMPTY_QUIZ: QuizState = {
  status: "idle",
  testId: null,
  attemptId: null,
  title: "",
  questions: [],
  answers: {},
  results: null,
  score: null,
  maxScore: null,
  message: null,
  produced: 0,
  total: 0,
};

const EMPTY_CARDS: CardsState = {
  status: "idle",
  setId: null,
  title: "",
  cards: [],
  message: null,
  produced: 0,
  total: 0,
};

export function useStudy() {
  const { send, ready } = useSocket();
  const [quiz, setQuiz] = useState<QuizState>(EMPTY_QUIZ);
  const [cards, setCards] = useState<CardsState>(EMPTY_CARDS);
  // Which generation each panel is currently watching. Progress for an
  // abandoned one must not overwrite the panel that replaced it.
  const quizId = useRef<string | null>(null);
  const cardsId = useRef<string | null>(null);

  const generateQuiz = useCallback(
    async (documentId: string, questionCount: number, difficulty: string) => {
      quizId.current = null;
      setQuiz({ ...EMPTY_QUIZ, status: "generating", total: questionCount });
      try {
        const { testId, title } = await createTest({
          documentId,
          questionCount,
          difficulty,
          // Multiple choice only. Written answers need a model to grade them,
          // which costs a call out of a small daily quota and returns a
          // judgement rather than a fact; a four-option question is graded
          // exactly and instantly.
          types: ["mcq"],
        });
        quizId.current = testId;
        setQuiz((state) => ({ ...state, testId, title }));
        if (ready) send({ type: "subscribe:generation", targetId: testId });
      } catch (err) {
        setQuiz({
          ...EMPTY_QUIZ,
          status: "failed",
          message: err instanceof Error ? err.message : "Could not start",
        });
      }
    },
    [ready, send],
  );

  const generateCards = useCallback(
    async (documentId: string, cardCount: number) => {
      cardsId.current = null;
      setCards({ ...EMPTY_CARDS, status: "generating", total: cardCount });
      try {
        const { setId, title } = await createFlashcardSet(documentId, cardCount);
        cardsId.current = setId;
        setCards((state) => ({ ...state, setId, title }));
        if (ready) send({ type: "subscribe:generation", targetId: setId });
      } catch (err) {
        setCards({
          ...EMPTY_CARDS,
          status: "failed",
          message: err instanceof Error ? err.message : "Could not start",
        });
      }
    },
    [ready, send],
  );

  useSocketEvent((event) => {
    if (event.type !== "generation:progress") return;
    const { targetId, kind, status, produced, total, message } = event;

    // Compared against a ref rather than inside the state updater: React may
    // call an updater twice, and fetching the finished paper twice would open
    // two attempts on it.
    const owned =
      kind === "test" ? quizId.current === targetId : cardsId.current === targetId;
    if (!owned) return;

    const patch = {
      produced,
      total: total || undefined,
      message: message ?? null,
      ...(status === "failed" ? { status: "failed" as StudyStatus } : {}),
    };

    if (kind === "test") {
      setQuiz((state) => ({ ...state, ...patch, total: patch.total ?? state.total }));
      if (status === "ready") void openQuiz(targetId);
    } else {
      setCards((state) => ({ ...state, ...patch, total: patch.total ?? state.total }));
      if (status === "ready") void openCards(targetId);
    }
  });

  /** Loads a finished paper and opens an attempt on it. */
  async function openQuiz(testId: string) {
    try {
      const [{ test, questions }, { attempt }] = await Promise.all([
        loadTest(testId),
        startAttempt(testId),
      ]);
      setQuiz((state) => ({
        ...state,
        status: "ready",
        testId,
        attemptId: attempt.id,
        title: test.title,
        questions,
        results: null,
        message: null,
      }));
    } catch (err) {
      setQuiz((state) => ({
        ...state,
        status: "failed",
        message: err instanceof Error ? err.message : "Could not open the quiz",
      }));
    }
  }

  async function openCards(setId: string) {
    try {
      const { set, cards: rows } = await loadFlashcardSet(setId);
      setCards((state) => ({
        ...state,
        status: "ready",
        setId,
        title: set.title,
        cards: rows,
        message: null,
      }));
    } catch (err) {
      setCards((state) => ({
        ...state,
        status: "failed",
        message: err instanceof Error ? err.message : "Could not open the cards",
      }));
    }
  }

  const answer = useCallback((questionId: string, response: string) => {
    setQuiz((state) => ({
      ...state,
      answers: { ...state.answers, [questionId]: response },
    }));
  }, []);

  const submit = useCallback(async () => {
    const { attemptId, questions, answers } = quiz;
    if (!attemptId) return;
    try {
      const result = await submitAttempt(
        attemptId,
        // Every question is sent, answered or not: a blank is graded as blank
        // so the denominator stays the whole paper.
        questions.map((q) => ({
          questionId: q.id,
          response: answers[q.id] ?? null,
        })),
      );
      setQuiz((state) => ({
        ...state,
        results: result.results,
        score: result.score,
        maxScore: result.maxScore,
      }));
    } catch (err) {
      setQuiz((state) => ({
        ...state,
        message: err instanceof Error ? err.message : "Grading failed",
      }));
    }
  }, [quiz]);

  const resetQuiz = useCallback(() => {
    quizId.current = null;
    setQuiz(EMPTY_QUIZ);
  }, []);
  const resetCards = useCallback(() => {
    cardsId.current = null;
    setCards(EMPTY_CARDS);
  }, []);

  return {
    quiz,
    cards,
    generateQuiz,
    generateCards,
    answer,
    submit,
    resetQuiz,
    resetCards,
  };
}
