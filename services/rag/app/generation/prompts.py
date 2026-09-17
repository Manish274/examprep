"""Prompts.

The grounding rules are constant. Explanation mode changes the *style* of an
answer -- how much detail, what register, how it is structured -- and never
what the model is permitted to assert. A "simple explanation" that invents a
plausible-sounding analogy from the model's own knowledge is exactly the
failure this system exists to prevent, and it is the mode most likely to
produce one.

The refusal token is a bare word rather than a sentence because it has to be
detectable in a stream before the answer is complete, and because a model
asked to "say you don't know" will otherwise hedge its way into answering
anyway.
"""

from __future__ import annotations

from app.core.models import ExplanationMode

UNSUPPORTED_TOKEN = "NOT_SUPPORTED"

_GROUNDING = f"""You answer questions using ONLY the study material provided below.

Rules, in order of importance:

1. Every factual claim must come from the provided sources. If the sources do
   not contain what is needed, reply with exactly {UNSUPPORTED_TOKEN} and
   nothing else. Do not answer from your own knowledge, even when you are
   confident and even when the question is simple.
2. Cite the source of each claim inline with its marker, like [S1] or [S2].
   A sentence carrying a fact needs a marker. Cite only markers that appear in
   the sources below.
3. If the sources partly answer the question, answer that part and say plainly
   which part the material does not cover. Do not fill the gap.
4. A source marked "read from an image" was transcribed from a picture by a
   model, not taken from the document's text. Use it, but if a claim rests only
   on such a source and the exact wording matters -- a formula, a number, a
   definition -- say that it was read from an image.
5. Never invent a source marker, a page number, or a quotation.
6. Earlier turns in this conversation tell you what the student is referring
   to. They are not evidence. Anything you asserted in an earlier answer must
   be supported by the sources below before you assert it again, and every
   citation must point at a source in this message -- markers from earlier
   answers referred to a different set of passages."""

_MODES: dict[ExplanationMode, str] = {
    ExplanationMode.SIMPLE: """Style: explain it simply.

Short sentences. Plain words. Define any technical term the moment you use it.
If an analogy genuinely helps, use one -- but the analogy must illustrate what
the sources actually say, never add anything they do not.

Aim for a few sentences, not paragraphs.""",
    ExplanationMode.DETAILED: """Style: explain it thoroughly.

Cover the mechanism, not just the definition: what it is, how it works, why it
matters, and how it relates to the other material in the sources. Use the
technical vocabulary of the sources and define it as you go.

Structure with short paragraphs or a list where that genuinely aids reading.""",
    ExplanationMode.EXAM: """Style: answer as if for an exam.

Lead with a precise definition. Then the key points a marker would look for,
as a short numbered or bulleted list. Include any formula, condition or worked
figure exactly as the sources give it.

Be complete but compact -- no preamble, no restating the question, no filler.""",
}


_OVERVIEW = f"""This question is about the material as a whole, and the sources
below are a spread across all of it rather than passages matched to the
question.

Questions like "what is most likely to be tested", "what is the hardest idea"
or "summarise this" ask you to judge the material, and the material will never
state the answer outright. That is expected: the judgment is yours to make,
and making it is the task. Base it on the sources -- what they spend the most
space on, what they define, list or explain step by step, which part has the
most moving pieces, what the other parts depend on. Pick one answer, say in a
sentence what the choice rests on, and present it as a judgment ("the idea
with the most steps in your notes is..."), not as a certainty.

Then answer it: summarise, explain or list the chosen material from the
sources. Every fact you mention still needs a citation, and you still may not
add anything the sources do not say. Do not reply {UNSUPPORTED_TOKEN} to a
question like this while there are sources below."""


OVERVIEW_RECHECK_NOTE = (
    "The sources above are a spread of the student's whole document. The "
    "question asks for your judgment about that material, which it will not "
    "state outright. Make the judgment from the sources, say what it rests "
    "on, and answer from them with citations. Do not reply "
    f"{UNSUPPORTED_TOKEN}."
)
"""Appended when a question about the whole document was refused anyway."""


def system_prompt(mode: ExplanationMode, *, overview: bool = False) -> str:
    parts = [_GROUNDING, _MODES[mode]]
    if overview:
        parts.append(_OVERVIEW)
    return "\n\n".join(parts)


def user_prompt(question: str, context: str) -> str:
    """Sources before the question.

    Long-context models attend better to instructions that come last, and this
    ordering keeps the question adjacent to the answer the model is about to
    write rather than buried above several thousand tokens of material.
    """
    return f"Study material:\n\n{context}\n\n---\n\nQuestion: {question}"


RECHECK_NOTE = (
    "The sources above were ranked as closely relevant to this question. Read "
    "them again carefully: if they explain the answer, even in different words "
    "from the question, answer from them and cite them. Reply "
    f"{UNSUPPORTED_TOKEN} only if they genuinely do not contain it."
)
"""Appended to the question when a refusal contradicts strong retrieval.

It does not relax the grounding rules -- the system prompt is unchanged -- it
only asks the model to look again at evidence the reranker rated highly.
"""


NO_CONTEXT_REPLY = (
    "I could not find anything in your uploaded material that answers this. "
    "It may be in a document you have not uploaded yet, or on a page that was "
    "an image with no readable text."
)

UNSUPPORTED_REPLY = (
    "Your uploaded material does not cover this. I have not answered from "
    "general knowledge, because an answer that is not in your notes will not "
    "help you in an exam on them."
)


CONDENSE_PROMPT = """Rewrite the follow-up question so it stands alone.

The student is mid-conversation, so their question may refer to earlier turns
("why does that matter?", "what about the second one?"). Rewrite it into a
single self-contained question that could be searched on its own.

Keep the student's own wording wherever possible. Do not answer it. Do not add
information. If the question is already self-contained, return it unchanged.

Conversation so far:
{history}

Follow-up question: {question}

Standalone question:"""


def condense_prompt(question: str, history: list[tuple[str, str]]) -> str:
    """Builds the query-rewriting prompt from recent turns.

    Only the last few exchanges are included: a long history costs tokens on
    every question and rarely changes what a follow-up refers to.
    """
    lines: list[str] = []
    for role, content in history[-6:]:
        speaker = "Student" if role == "user" else "Assistant"
        lines.append(f"{speaker}: {content[:400]}")
    return CONDENSE_PROMPT.format(
        history="\n".join(lines) or "(no earlier turns)", question=question
    )
