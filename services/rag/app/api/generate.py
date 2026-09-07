"""Generation endpoints for tests, grading and flashcards.

Called by the Node worker, which owns persistence. This service reads the
indexed chunks, generates, and hands the result back -- it never learns what a
test or a student is.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import CorrelationId, InternalAuth, SettingsDep
from app.container import get_container
from app.core.models import QuestionType
from app.generation.exams import ExamGenerator
from app.generation.flashcards import FlashcardGenerator
from app.generation.grading import GradableAnswer, Grader

logger = logging.getLogger(__name__)

router = APIRouter(tags=["generation"], dependencies=[InternalAuth])


async def _load_chunks(user_id: str, document_ids: list[str] | None, limit: int):
    container = get_container()
    chunks = await container.store.sample_chunks(
        user_id=user_id, document_ids=document_ids, limit=limit
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
async def generate_test(
    request: GenerateTestRequest,
    settings: SettingsDep,
    correlation_id: CorrelationId = None,
) -> GenerateTestResponse:
    container = get_container()
    # Sampled wider than the question count so the generator can skip passages
    # carrying nothing examinable.
    chunks = await _load_chunks(
        request.user_id, request.document_ids, request.question_count * 4
    )

    result = await ExamGenerator(container.llm).generate(
        chunks,
        question_count=request.question_count,
        types=request.types,
        difficulty=request.difficulty,
    )

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

    logger.info(
        "generated %s questions (correlation_id=%s)",
        len(result.questions),
        correlation_id,
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
    settings: SettingsDep,
    correlation_id: CorrelationId = None,
) -> GenerateFlashcardsResponse:
    container = get_container()
    chunks = await _load_chunks(
        request.user_id, request.document_ids, request.card_count * 3
    )

    result = await FlashcardGenerator(container.llm).generate(
        chunks, card_count=request.card_count
    )

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
            CardResponse(
                front=c.front, back=c.back, source_chunk_id=c.source_chunk_id
            )
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
async def grade(
    request: GradeRequest,
    settings: SettingsDep,
    correlation_id: CorrelationId = None,
) -> GradeResponse:
    if not request.answers:
        raise HTTPException(400, "No answers to grade")

    container = get_container()
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


@router.get("/generate/config")
async def generation_config(settings: SettingsDep) -> dict[str, Any]:
    return {
        "model": settings.LLM_MODEL,
        "question_types": [t.value for t in QuestionType],
    }
