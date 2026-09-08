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

# "[S1]", "[S12]" -- the citation markers the pipeline itself emits.
_MARKER = re.compile(r"\[S\d+\]")
# Two or more blank lines left behind after stripping markers mid-paragraph.
_GAPS = re.compile(r"[ \t]{2,}")


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
