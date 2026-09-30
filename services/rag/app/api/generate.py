"""Generation endpoints for tests, grading and flashcards.

Called by the Node side, which owns persistence: the worker generates, the API
grades. This service reads the indexed chunks, generates, and hands the result
back -- it never learns what a test or a student is.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import InternalAuth
from app.container import get_container
from app.core.models import Chunk, QuestionType
from app.generation.exams import ExamGenerator
from app.generation.flashcards import FlashcardGenerator
from app.generation.grading import GradableAnswer, Grader
from app.observability.trace import identify, span

router = APIRouter(tags=["generation"], dependencies=[InternalAuth])

# Chunks read for one quiz or card set: in practice the whole document, so the
# generator can choose from all of it rather than from a fixed corner.
_GENERATION_SCAN = 2000


async def _load_chunks(user_id: str, document_ids: list[str] | None) -> list[Chunk]:
    container = get_container()
    chunks = await container.store.sample_chunks(
        user_id=user_id, document_ids=document_ids, limit=_GENERATION_SCAN
    )
    if not chunks:
        raise HTTPException(
            404,
            "No indexed material found. Upload a document and wait for it to "
            "finish processing.",
        )
    return chunks


class GenerateTestRequest(BaseModel):
    user_id: str
    document_ids: list[str] | None = None
    question_count: Annotated[int, Field(ge=1, le=50)] = 10
    types: list[QuestionType] | None = None
    difficulty: str = "mixed"
    # Chunks earlier quizzes and card sets on this document were written from,
    # to be drawn on again only once the rest are used up.
    avoid_chunk_ids: Annotated[list[str], Field(max_length=5000)] = []


class QuestionResponse(BaseModel):
    type: QuestionType
    prompt: str
    options: list[str] | None
    # For MCQ this is the index into options, as a string; for the others it is
    # the answer itself.
    correct_answer: str
    explanation: str
    source_chunk_id: str


class GenerateTestResponse(BaseModel):
    questions: list[QuestionResponse]
    stats: dict[str, int]


@router.post("/generate/test", response_model=GenerateTestResponse)
async def generate_test(request: GenerateTestRequest) -> GenerateTestResponse:
    container = get_container()
    identify(user_id=request.user_id)
    chunks = await _load_chunks(request.user_id, request.document_ids)

    async with span(
        "generate_questions",
        requested=request.question_count,
        chunks=len(chunks),
        difficulty=request.difficulty,
    ) as observed:
        result = await ExamGenerator(container.llm).generate(
            chunks,
            question_count=request.question_count,
            types=request.types,
            difficulty=request.difficulty,
            avoid=set(request.avoid_chunk_ids),
        )
        observed.output(**result.as_dict())

    if not result.questions:
        # Blaming the material when every call was rate limited sends the
        # student to re-upload a perfectly good document. Distinguish the two.
        if result.batches_run == 0 and result.failed_batches:
            raise HTTPException(
                503,
                "The generation service was unavailable (rate limited). "
                "Nothing is wrong with your document -- try again shortly.",
            )
        raise HTTPException(
            422,
            "No questions could be generated from this material. It may be too "
            "short, or contain mostly headings and images.",
        )

    return GenerateTestResponse(
        questions=[
            QuestionResponse(
                type=q.type,
                prompt=q.prompt,
                options=q.options,
                correct_answer=q.correct_answer,
                explanation=q.explanation,
                source_chunk_id=q.source_chunk_id,
            )
            for q in result.questions
        ],
        stats=result.as_dict(),
    )


class GenerateFlashcardsRequest(BaseModel):
    user_id: str
    document_ids: list[str] | None = None
    card_count: Annotated[int, Field(ge=1, le=100)] = 20
    avoid_chunk_ids: Annotated[list[str], Field(max_length=5000)] = []


class CardResponse(BaseModel):
    front: str
    back: str
    source_chunk_id: str


class GenerateFlashcardsResponse(BaseModel):
    cards: list[CardResponse]
    stats: dict[str, int]


@router.post("/generate/flashcards", response_model=GenerateFlashcardsResponse)
async def generate_flashcards(
    request: GenerateFlashcardsRequest,
) -> GenerateFlashcardsResponse:
    container = get_container()
    identify(user_id=request.user_id)
    chunks = await _load_chunks(request.user_id, request.document_ids)

    async with span(
        "generate_cards", requested=request.card_count, chunks=len(chunks)
    ) as observed:
        result = await FlashcardGenerator(container.llm).generate(
            chunks,
            card_count=request.card_count,
            avoid=set(request.avoid_chunk_ids),
        )
        observed.output(**result.as_dict())

    if not result.cards:
        if result.batches_run == 0 and result.failed_batches:
            raise HTTPException(
                503,
                "The generation service was unavailable (rate limited). "
                "Nothing is wrong with your document -- try again shortly.",
            )
        raise HTTPException(
            422,
            "No flashcards could be generated from this material. It may be too "
            "short, or contain mostly headings and images.",
        )

    return GenerateFlashcardsResponse(
        cards=[
            CardResponse(front=c.front, back=c.back, source_chunk_id=c.source_chunk_id)
            for c in result.cards
        ],
        stats=result.as_dict(),
    )


class AnswerInput(BaseModel):
    question_id: str
    question_type: QuestionType
    prompt: str
    correct_answer: str
    explanation: str
    response: str | None = None
    options: list[str] | None = None
    source_text: str = ""


class GradeRequest(BaseModel):
    # Whose attempt this is. The trace is filed under it, so it is deleted
    # with the rest of the visit rather than outliving it.
    user_id: str
    answers: list[AnswerInput]


class GradedResponse(BaseModel):
    question_id: str
    is_correct: bool
    awarded: float
    feedback: str


class GradeResponse(BaseModel):
    graded: list[GradedResponse]
    score: float
    max_score: float


@router.post("/grade", response_model=GradeResponse)
async def grade(request: GradeRequest) -> GradeResponse:
    identify(user_id=request.user_id)
    if not request.answers:
        raise HTTPException(400, "No answers to grade")

    container = get_container()
    written = sum(1 for a in request.answers if a.question_type == "short_answer")
    async with span(
        "grade",
        answers=len(request.answers),
        # Only the written ones cost a model call; the split explains the
        # latency of an otherwise identical-looking submission.
        written=written,
    ) as observed:
        graded = await Grader(container.llm).grade(
            [
                GradableAnswer(
                    question_id=a.question_id,
                    question_type=a.question_type,
                    prompt=a.prompt,
                    correct_answer=a.correct_answer,
                    explanation=a.explanation,
                    response=a.response,
                    options=a.options,
                    source_text=a.source_text,
                )
                for a in request.answers
            ]
        )
        observed.output(
            correct=sum(1 for g in graded if g.is_correct),
            awarded=round(sum(g.awarded for g in graded), 2),
            out_of=len(graded),
        )

    return GradeResponse(
        graded=[
            GradedResponse(
                question_id=g.question_id,
                is_correct=g.is_correct,
                awarded=g.awarded,
                feedback=g.feedback,
            )
            for g in graded
        ],
        # Partial credit is summed rather than counting correct answers, so a
        # student who half-answered several questions sees that reflected.
        score=round(sum(g.awarded for g in graded), 2),
        max_score=float(len(graded)),
    )
