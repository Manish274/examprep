"""Questions about the material as a whole.

"Summarise what I uploaded", "what am I most likely to be tested on?",
"explain the hardest idea in my notes" -- these name no subject, so there is
nothing for retrieval to match. Searching for "hardest idea in my notes"
returns whichever chunks happen to sit nearest that phrase, the relevance
scores are low, and the question is refused as off-topic: the product's own
starter prompts were answered with "not in your material".

Such a question is answered from an even spread of the whole document
instead, and the model is told it may judge the material -- what matters
most, what is hardest -- as long as every judgment rests on cited passages.

Recognition is lexical, not a model call: it runs on every question, and a
misfire in either direction is cheap. A question that names a subject
("summarise how the ESP8266 sends data") is left to retrieval, which will
find that subject far better than a sample will.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z0-9]+")

# What makes it a question about the whole: summarising, predicting the exam,
# ranking by difficulty or importance.
_INTENT = re.compile(
    r"\b("
    r"summar(y|ies|ise|ize|ised|ized|ising|izing)|overview|recap|gist|tl ?dr"
    r"|key (points?|ideas?|concepts?|takeaways?|topics?|terms?)"
    r"|main (points?|ideas?|topics?|concepts?|themes?|takeaways?)"
    r"|(what|which) (is|are) (this|these|it|they|the|my)( [a-z]+)? about"
    r"|what (does|do) (this|these|it|they|the|my)( [a-z]+)?"
    r" (cover|say|talk about|discuss)"
    r"|what (did|have) i (upload|uploaded|add|added)"
    r"|tested on|tested about|examined on|in the exam|on the exam|exam questions?"
    r"|likely to (be )?(ask|asked|test|tested|come|come up|appear)"
    r"|(most )?important (topics?|points?|ideas?|concepts?|parts?|things?)"
    r"|most important|focus on|revise first|study first|priorit(y|ies|ise|ize)"
    r"|hardest|toughest|trickiest|most (difficult|complex|confusing|challenging)"
    r"|(difficult|hard|tricky|confusing|complex) (idea|concept|part|topic|bit)s?"
    r")\b"
)

# Words that can appear in such a question without naming a subject. Anything
# outside this list is taken as a subject, and the question goes to retrieval.
_GENERIC = frozenset(
    """
    a an the this that these those it its they them their there here
    i me my mine we us our you your
    is are was were be been being am do does did doing done have has had
    can could would should will shall may might must
    what which who whom whose why how when where
    of in on at to for from by with about into over under up out as and or
    but so if then than also just only really very please
    most more much many some any all every each whole entire overall
    likely probably expect expected
    give tell show explain describe list summarise summarize summary
    summarised summarized summarising summarizing summaries overview recap
    gist tl dr key main important importance priority priorities prioritise
    prioritize focus revise revision study studying learn learning
    first next best top
    point points idea ideas concept concepts topic topics theme themes
    takeaway takeaways term terms part parts thing things bit bits
    exam exams test tests tested testing examined examination quiz asked ask
    questions question come comes coming appear appears
    hard hardest tough toughest tricky trickiest difficult complex confusing
    challenging simply simple simpler plain briefly brief short quick quickly
    detail detailed
    notes note material materials document documents doc docs file files
    upload uploaded uploads added add deck decks slides slide pdf pdfs
    lecture lectures chapter chapters presentation presentations content
    contents cover covers covered say says talk talks discuss discusses
    know need understand get go
    s t m ll re ve d
    """.split()
)


def is_overview_question(question: str) -> bool:
    """Whether a question asks about the material as a whole rather than
    about a subject within it."""
    text = question.lower().replace("’", "'")
    if not _INTENT.search(" ".join(_WORD.findall(text))):
        return False
    return all(word in _GENERIC for word in _WORD.findall(text))
