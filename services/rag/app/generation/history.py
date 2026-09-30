"""Conversation history, prepared for the answering model.

Until now history was used for one thing only: rewriting a follow-up into a
standalone question so that retrieval worked. That makes *retrieval* aware of
the conversation while leaving the model answering it blind -- so "explain that
more simply" retrieved the right passages and then produced an answer written
as though the student had never asked anything before.

Three things have to be true of the turns handed to the model:

**They must not crowd out the material.** Context is the answer's evidence;
history is only its setting. History is trimmed newest-first against its own
budget so a long conversation can never squeeze the sources out.

**They must not carry stale citations.** A previous answer says "[S1]", and
that marker referred to whatever was retrieved *then*. Retrieval for the new
question returns a different set, so a model that copies the old marker
produces a citation resolving to an unrelated passage -- a wrong source shown
to the student with full confidence. Markers are stripped from prior turns.

**They must not become evidence.** An earlier answer is not a source. The
grounding rules say so explicitly; this module makes it structurally harder by
keeping history and sources visibly separate.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.tokenizer import TokenCounter, get_token_counter

# "[S1]", "[S1, S2]" -- the citation markers the pipeline itself emits.
from app.generation.context import MARKER_GROUP as _MARKER

# Two or more blank lines left behind after stripping markers mid-paragraph.
_GAPS = re.compile(r"[ \t]{2,}")

# Contractions split at the apostrophe: "that's" -> "that", "s".
_WORD = re.compile(r"[a-z0-9]+")

# Words that only mean something against an earlier turn.
_REFERRING = frozenset(
    """it its itself they them their theirs themselves this that these
    those he him his she her hers here there former latter above same such one
    ones else another other previous earlier mentioned said""".split()
)

# Openings that continue the previous turn rather than start a new one.
_CONTINUATION = re.compile(
    r"^(and|but|so|also|then|or|what about|how about|more|elaborate"
    r"|explain (more|further|again)|continue|go on|examples?|in simpler?"
    r"|again)\b"
)

# Question scaffolding and instruction verbs: none of them names a subject.
_FILLER = frozenset(
    """a an the of in on to for with by from as at into about is are was were be
    been being do does did can could would should will may might has have had
    what which who whom whose when where how why please me i my we our you your
    tell give list describe explain show define summarise summarize
    s t d m ll re ve don doesn didn isn aren wasn weren""".split()
)

# Words that ask for an aspect of something without naming the something:
# "what are the advantages?", "give an example", "explain the working".
_ASPECT = frozenset(
    """advantage advantages disadvantage disadvantages benefit benefits drawback
    drawbacks limitation limitations pro pros con cons type types kind kinds
    example examples instance step steps stage stages phase phases use uses
    usage application applications feature features property properties
    characteristic characteristics difference differences similarity
    similarities cause causes effect effects importance purpose meaning
    definition significance role roles function functions component components
    part parts process method methods rule rules reason reasons working works
    work detail details point points summary history formula formulas equation
    value values impact result results problem problems solution solutions idea
    ideas concept concepts topic topics main key first second third last next
    simply simple simpler briefly brief short detailed further better clearly
    words terms mean means""".split()
)

# A follow-up naming at least this many subject words stands alone whatever
# the conversation was about.
MIN_STANDALONE_WORDS = 5


@dataclass
class PreparedTurn:
    role: str
    content: str


def strip_markers(text: str) -> str:
    """Removes citation markers from a previous answer.

    The sources they pointed at are not the sources in front of the model now.
    Leaving them in invites the model to reuse a marker whose meaning has
    silently changed underneath it.
    """
    return _GAPS.sub(" ", _MARKER.sub("", text)).strip()


def prepare(
    turns: Sequence[tuple[str, str]],
    *,
    max_turns: int = 8,
    max_tokens: int = 1500,
    max_chars_per_turn: int = 1500,
    counter: TokenCounter | None = None,
) -> list[PreparedTurn]:
    """Trims a conversation to what is worth sending, newest first.

    Returned in chronological order, because that is the order a conversation
    happened in and the order the model is trained to read.

    A turn that would break the budget stops the walk rather than being
    truncated mid-sentence: half an exchange reads as though the model
    misremembered what was said, which is worse than not including it.
    """
    if not turns or max_turns <= 0 or max_tokens <= 0:
        return []

    count = (counter or get_token_counter()).count
    kept: list[PreparedTurn] = []
    budget = max_tokens

    for role, content in reversed(list(turns)):
        if len(kept) >= max_turns:
            break

        normalised = role if role in ("user", "assistant") else "user"
        text = content if normalised == "user" else strip_markers(content)
        # A single very long answer would otherwise consume the whole budget
        # and evict every other turn with it.
        text = text[:max_chars_per_turn].strip()
        if not text:
            continue

        cost = count(text)
        if cost > budget:
            break

        budget -= cost
        kept.append(PreparedTurn(role=normalised, content=text))

    kept.reverse()

    # A conversation that starts on an assistant turn reads as a reply to
    # something the model cannot see. Drop it and start at the question.
    while kept and kept[0].role == "assistant":
        kept.pop(0)

    return kept


def _stem(word: str) -> str:
    """Crude plural folding, so "colleges" in a question meets "college" in
    the answer before it."""
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def needs_context(question: str, history: Sequence[tuple[str, str]] = ()) -> bool:
    """Whether a follow-up depends on the turns before it.

    Deciding this locally skips the rewrite for a question that already stands
    alone. The rewrite is a call on the utility model's small daily quota, and
    it adds a second or more to every follow-up it runs on.

    Biased toward rewriting, because the two mistakes are not equal: a
    question that needed rewriting and did not get one retrieves worse, while
    one rewritten needlessly only costs the call. So the rewrite runs on any
    referring word ("how does *it* work"), any continuing opening ("and
    then?"), and any question that names only an aspect ("what are the
    advantages?"). A short question that names a subject runs it only when the
    subject is what the conversation was already about: "when was the college
    founded?" after an answer about a college means that college, while "what
    is Pharmakon?" after an answer about something else means just what it
    says.
    """
    text = question.strip().lower()
    words = _WORD.findall(text)

    if any(word in _REFERRING for word in words):
        return True
    if _CONTINUATION.match(text):
        return True

    subject = [word for word in words if word not in _FILLER]
    named = [word for word in subject if word not in _ASPECT and not word.isdigit()]
    if not named:
        return True
    if len(subject) >= MIN_STANDALONE_WORDS:
        return False

    # The last exchange is what a short follow-up leans on.
    recent = {
        _stem(word)
        for _, content in history[-2:]
        for word in _WORD.findall(content.lower())
    }
    return any(_stem(word) in recent for word in named)
