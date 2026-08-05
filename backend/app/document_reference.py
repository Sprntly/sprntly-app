"""Which document is the user TALKING ABOUT?

`ask_runner.document_grounding` already answers a different, easier question:
which documents is this message *about*, by fused lexical+semantic rank
(Stage T). That stage deliberately has no score floor — a candidate arrives as
"here is something that may bear on this, ignore it if it doesn't", the prompt
carries an explicit ignore-if-irrelevant rule, and a wrong load costs budget
rather than correctness.

This module answers the HARDER question, and it is a different kind of claim:

    "summarize the Q3 pricing teardown"     -> the user MEANS one document
    "what does it say about discounts?"     -> the user means the one from
                                               earlier in this thread

Resolving that is a statement to the model that *this is the document the user
asked for*, and a wrong one is not a wasted slot — it is a confident answer
about the wrong page. So the two stages need opposite failure modes, and that
is why this is a separate stage rather than a threshold bolted onto Stage T.

ABSTENTION IS THE FEATURE. Every ambiguity here resolves to NOTHING. Abstaining
does not blank the prompt: Stage T still runs exactly as it does today, so the
worst case is the behaviour that shipped last week, never a document falsely
labelled as the one the user meant.

THE OVERREACH BUG THIS IS BUILT NOT TO REPEAT. `call_index.resolve_calls` — the
precedent this follows — matched query terms as bare SUBSTRINGS. The term "can",
three characters, survived its stopword strip and sat inside "Candidate", so
"can you summarize our recent customer calls" scored an internal SE-candidate
interview as a NAMED match, resolved to that one call, and summarized it: a
plural, general question answered from a single unrelated transcript. The fix
(require a named reference, prefer whole words, floor substring terms at 4
chars) is not retrofitted here — it is the starting point:

  * `_MIN_SUBSTRING_TERM` — a term is trusted mid-word only at >= 4 chars.
    Whole-word hits are trusted at any length, because "SSO", "API" and "Q3"
    are real document names.
  * `_MIN_PIN_SCORE` — a substring hit alone can never PIN a document. Pinning
    requires at least one whole-word match.
  * `_AMBIGUITY_MARGIN` — the winner must beat the runner-up by a clear whole
    word, or nothing is pinned. Two similarly-named pages are the case this
    exists for.
  * ask-word and generic-word stripping, so a message that names nothing
    survives nothing — and a message that survives nothing has not made a
    reference, whatever verbs it used.

TENANCY IS INHERITED, NEVER RE-DERIVED. `resolve_documents` takes the candidate
rows as an ARGUMENT and never reads the catalog itself. The caller has already
applied the company filter and the conversation-ownership join (see
`document_catalog.list_documents`), so there is no query in this module that
could forget them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

#: Minimum length before a term is trusted as a MID-WORD match. Whole-word hits
#: are trusted at any length. This is the `call_index` floor, and it exists for
#: the "can" inside "Candidate" failure described in the module docstring.
_MIN_SUBSTRING_TERM = 4

#: Score for one whole-word hit against a document's title, source or topics.
_WHOLE_WORD_SCORE = 3
#: Score for one distinctive mid-word hit. Deliberately far below
#: `_MIN_PIN_SCORE`: substrings can help RANK, they can never PIN.
_SUBSTRING_SCORE = 1
#: Score for the whole surviving query phrase appearing in the document.
_PHRASE_SCORE = 10

#: The floor to pin a document at all — one whole word, minimum. A document
#: reached only by substring hits is never the resolved referent, however many
#: of them there are.
_MIN_PIN_SCORE = _WHOLE_WORD_SCORE

#: TITLE whole-word hits required to pin. TWO, not one, because one common
#: word shared with a title is not a reference to it — and no stopword list is
#: ever complete enough to make it one. Against a real workspace, "how many
#: customers do we have?" pinned "Template - How-to guide" on the single word
#: "how"; adding "how" to _ASK_WORDS fixed that instance, and this fixes the
#: CLASS, which is what matters when the next title contains "when" or "get".
_MIN_PIN_TITLE_WORDS = 2

#: …unless the message also carries a DOCUMENT CUE, in which case one title
#: word is enough.
#:
#: This replaces a length-based escape hatch (any single whole-word hit of >= 6
#: chars could pin). That hatch reopened the exact class it was added beside:
#: "how is onboarding going for new hires?" pinned a page titled "Onboarding",
#: and "what's our pricing strategy for enterprise?" pinned a teardown whose
#: TOPICS carried "pricing". Length is the wrong signal — "onboarding" is a
#: long word and an ordinary one.
#:
#: The right signal is whether the message is talking ABOUT A DOCUMENT at all.
#: "the hiring PAGE", "what's in the DOC" refer; "how is onboarding going"
#: does not, however long its words are. Questions that merely relate to a
#: document are Stage T's job — it has no floor and an explicit
#: ignore-if-irrelevant rule, which is the right contract for them.
_DOCUMENT_CUE = re.compile(
    r"\b(?:doc|docs|document|documents|file|files|page|pages|wiki|deck|decks|"
    r"pdf|spec|specs|report|reports|guide|guides|runbook|runbooks|memo|memos|"
    r"write-?up|one-?pager|slide|slides|sheet|sheets|attachment|attachments|"
    r"confluence|drive|gdrive|notion)\b",
    re.I,
)

#: How far the winner must beat the runner-up. One whole word of separation:
#: below that the two documents are matched on the same words and choosing
#: between them would be a coin flip wearing a score.
_AMBIGUITY_MARGIN = _WHOLE_WORD_SCORE

#: Most documents one reference may resolve to. A reference is singular by
#: nature ("the pricing doc"); this is a backstop, not a fan-out.
MAX_RESOLVED_DOCUMENTS = 2

#: Heading of the block that says a reference could NOT be resolved.
#:
#: Lives here, in the leaf, for two reasons. `ask_runner` re-exports it (so
#: `ask_runner.UNRESOLVED_REFERENCE_HEADING` still resolves for anything
#: binding to it there), and this module needs it itself: it is the MARKER
#: that lets a later turn recognise that the assistant asked "which document?"
#: and read the user's answer.
#:
#: A CROSS-PR CONTRACT. Three places bind to it verbatim: `document_grounding`
#: renders it, `prompts.ASK_SYSTEM_DOCUMENTS_ADDENDUM` rule 11 quotes it, and
#: #1060's live-sweep addendum carries a precedence clause naming it. Rewording
#: means rewording all of them; tests here and in #1060 assert the literal.
UNRESOLVED_REFERENCE_HEADING = "The document this message refers to is UNRESOLVED"

#: Ordinal selections in a reply to our clarifying question. "one" is
#: deliberately ABSENT: in a reply it almost always means "item" ("the Q4
#: one"), not "the first", and mapping it to 0 silently selects the wrong
#: candidate instead of falling through to term-narrowing. Same trap, and the
#: same resolution, as `call_index._ORDINALS`.
_ORDINALS = {
    "first": 0, "1st": 0, "1": 0,
    "second": 1, "2nd": 1, "2": 1,
    "third": 2, "3rd": 2, "3": 2,
    "last": -1, "latest": 0, "newest": 0, "oldest": -1,
}

#: A reply short enough to BE a selection. "the first one" selects; "what did
#: the first customer say about pricing" is a new question that happens to
#: contain "first".
_MAX_SELECTION_WORDS = 5

#: How far back to look for an established referent. Beyond this the "it" in a
#: message is not plausibly pointing at a document nobody has mentioned in
#: twenty turns.
_HISTORY_LOOKBACK_TURNS = 12

# Words that describe the ASK rather than the document. Stripped before we ask
# "did this message name anything?", so "can you summarize the pricing doc"
# is matched on "pricing" alone.
#
# The second block is the polite/verb-particle wrapping a request carries —
# the block whose absence is what let "can" through in `call_index`.
_ASK_WORDS = frozenset({
    "summarize", "summarise", "summary", "recap", "tell", "me", "about", "the",
    "a", "an", "of", "for", "on", "in", "with", "and", "from", "our", "their",
    "this", "that", "it", "its", "what", "was", "were", "did", "does", "do",
    "we", "say", "says", "said", "state", "states", "mention", "mentions",
    "cover", "covers", "discuss", "discussed", "explain", "explains",
    "according", "per", "read", "open", "find", "look", "check", "see",
    # Interrogatives and auxiliaries. Absent at first, and "how" is why: it is
    # a whole word inside the title "Template - How-to guide", so
    # "how many customers do we have?" — a question about the BUSINESS, naming
    # no document — scored a whole-word hit and pinned that page. Caught by
    # running the resolver against a real workspace's titles rather than
    # against invented ones, which is the whole reason to do that.
    "how", "why", "when", "where", "who", "whom", "whose", "which",
    "is", "are", "am", "be", "been", "being", "has", "have", "had",
    "many", "much", "there", "here", "than", "then", "but", "not", "get",
    "got", "make", "makes", "made", "know", "think", "like", "want",
    # Request wrapping and verb particles. "can" is here for the reason the
    # module docstring gives.
    "can", "could", "would", "will", "you", "your", "let", "lets", "want",
    "need", "please", "give", "show", "get", "pull", "up", "over", "into",
    "through", "walk", "dig", "run", "help", "again", "also", "just",
    # Document nouns. Among hundreds of documents these name nothing — every
    # document is a "doc". Narrowing BETWEEN candidates keeps them (see
    # `_SELECTION_STOPWORDS`), which is that set's job, not this one's.
    "doc", "docs", "document", "documents", "file", "files", "page", "pages",
    "wiki", "confluence", "drive", "gdrive", "google", "sheet", "sheets",
    "slide", "slides", "deck", "pdf", "attachment", "attachments", "content",
    "contents", "text", "copy",
})

# Words that describe a document GENERICALLY — recency, kind, who wrote it in
# the abstract — and so can never name a particular one. Same mechanism as
# `_ASK_WORDS`, split out because the reason differs: an ask-word describes the
# request, a generic word describes the document but identifies none.
#
# A message whose only survivors are "recent" and "internal" has named nothing,
# and must reach the no-reference branch rather than pinning whatever ranks
# first.
_GENERIC_DOCUMENT_WORDS = frozenset({
    # recency / quantity
    "recent", "recently", "latest", "last", "past", "previous", "prior",
    "few", "couple", "several", "some", "any", "all", "every", "each",
    "more", "most", "other", "another", "next", "new", "old", "older",
    "newest", "oldest", "first", "second", "third",
    # who, in the abstract
    "customer", "customers", "client", "clients", "user", "users", "team",
    "teams", "everyone", "anyone", "someone", "internal", "external",
    "company", "companies", "workspace", "everything", "anything",
    # when
    "today", "yesterday", "day", "days", "week", "weeks", "month", "months",
    "quarter", "quarters", "year", "years", "ago", "current", "currently",
})

# An anaphor that NAMES a document kind — "that page", "this document", "the
# doc". Self-evidently about a document, so it needs no corroboration.
_EXPLICIT_DOC_ANAPHORA = re.compile(
    r"\bth(?:at|is|ose|ese)\s+"
    r"(?:doc|docs|document|documents|file|files|page|pages|wiki|deck|pdf|"
    r"spec|report)\b"
    r"|\bthe\s+(?:doc|document|file|page|deck|pdf|spec|report|above)\b",
    re.I,
)

# A BARE pronoun. Referential, but it does not say referential to WHAT — so on
# its own it is not evidence of a document reference. A bare "this"/"that" is
# admitted only as the object of a preposition or at the very end of the
# message; anywhere else it swallows "I think that we should ship".
_BARE_ANAPHORA = re.compile(
    r"\b(?:it|its|it'?s|they|them|those|these)\b"
    r"|\bthe\s+(?:same|one)\b"
    r"|\b(?:on|about|for|of|with|from|into|in)\s+th(?:is|at)\b"
    r"|\bth(?:is|at)\s*[?.!]*\s*$",
    re.I,
)

# What corroborates a BARE pronoun into a document reference: a verb about
# READING something. Same pairing `skill_router` uses (`_TRACKER_ANAPHORA` +
# `_TRACKER_DETAIL`), for the same reason.
#
# Without this pairing, "how many seats is it?" matched on "it", found nothing
# established, and printed an UNRESOLVED "which document do you mean?" section
# onto an ordinary question. A bare pronoun is far too common to treat as a
# document reference by itself, and a spurious abstention is a worse failure
# than a missed resolution — it makes chat look confused about a question it
# understood perfectly.
_DOC_READ_CUE = re.compile(
    r"\b(?:say|says|said|state|states|stated|mention|mentions|mentioned|"
    r"cover|covers|covered|discuss|discusses|discussed|describe|describes|"
    r"according|read|reads|section|sections|detail|details|elaborate|"
    r"expand|summar\w*|recap|explain|explains|content|contents|"
    r"more|written|writes|wrote|conclude|concludes|recommend\w*)\b",
    re.I,
)


def _has_bare_anaphora_about_a_document(text: str) -> bool:
    return bool(_BARE_ANAPHORA.search(text) and _DOC_READ_CUE.search(text))

# Function words only — the looser strip used when narrowing BETWEEN a handful
# of candidates, where document nouns are often the only discriminating term
# ("the pricing PAGE" vs "the pricing DECK"). Mirrors
# `call_index._SELECTION_STOPWORDS` and its reasoning exactly.
_SELECTION_STOPWORDS = frozenset({
    "the", "that", "this", "one", "ones", "please", "and", "for", "with",
    "about", "give", "show", "summarize", "summarise", "summary",
})


@dataclass(frozen=True)
class DocumentReference:
    """What a message refers to, or the stated reason we would not guess.

    Three outcomes, and callers must tell them apart:

        referenced=False                 the message points at no document.
                                         Not a failure — most messages don't.
        referenced=True, documents=[...] resolved. These ARE what the user
                                         means; say so to the model.
        referenced=True, abstained=True  the message points at a document and
                                         we could not tell which. Pin nothing.

    The third is the one this module exists for. `candidates` carries what we
    were torn between so the caller can offer them instead of guessing.
    """

    documents: list[Any] = field(default_factory=list)
    #: "named" (the message spells it out) or "anaphoric" (established earlier
    #: in the thread). Empty when nothing was referenced.
    basis: str = ""
    #: True when a reference was detected at all.
    referenced: bool = False
    #: True when a reference was detected and deliberately NOT resolved.
    abstained: bool = False
    #: Model- and log-facing. Why we would not pick, in a sentence.
    reason: str = ""
    #: What we were choosing between when we abstained.
    candidates: list[Any] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return bool(self.documents)


def _norm(text: str) -> str:
    """Lowercase alphanumerics only, so 'Q3 Pricing', 'q3-pricing' and
    'Q3_Pricing' all compare equal."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _words(text: str) -> set[str]:
    """`text` as normalized whole words, empties dropped."""
    return {_norm(w) for w in re.findall(r"[A-Za-z0-9]+", text or "")} - {""}


def _is_naming_token(word: str) -> bool:
    """Could this token be part of a document's name?

    Three characters is the general floor — two-letter words are function
    words or noise, and admitting them would put the substring guard under
    pressure for nothing. The exception is a short token CARRYING A DIGIT:
    "Q3", "Q4", "V2", "K8" are how documents are actually named and versioned,
    and they are the single most discriminating token in the titles that carry
    them. Dropping them is what made "the Q4 pricing teardown" indistinguishable
    from "the Q3 pricing teardown" — both abstained, on the one word that told
    them apart.
    """
    if len(word) > 2:
        return True
    return len(word) == 2 and any(ch.isdigit() for ch in word)


#: Characters of any one conversation turn that reference resolution reads.
#:
#: `routes.ask._load_history` folds ATTACHMENT TEXT into a turn's content,
#: unclamped, so a thread carrying an imported PRD or PDF can put tens of
#: thousands of characters into one turn. Measured before this cap: a 56 KB
#: attachment against a 200-row catalog cost 1.09 s of synchronous CPU inside
#: the request, before any model call, scaling linearly with the attachment.
#:
#: A reference lives in the words the user typed, not in a pasted appendix, so
#: reading the head of each turn loses nothing this stage needs while bounding
#: the cost at roughly a page of prose per turn.
_MAX_TURN_CHARS = 2_000


def _clamp_turn(text: str) -> str:
    return (text or "")[:_MAX_TURN_CHARS]


def query_terms(question: str) -> list[str]:
    """The words that plausibly NAME a document, DEDUPED and order-preserving.

    An empty result is the signal that the message named nothing — "can you
    summarize our recent internal docs" leaves nothing behind, which is exactly
    right, because it names no document. Callers must treat empty as
    "no named reference", never as "match everything".

    Deduping is not tidiness. Hits were counted per OCCURRENCE, so a message
    repeating one word ("pricing, pricing, pricing") could satisfy the
    two-title-word reference gate from a single distinct word — the gate
    bypassed by repetition rather than by evidence.
    """
    out: list[str] = []
    seen: set[str] = set()
    for word in re.findall(r"[A-Za-z0-9]+", question or ""):
        lowered = word.lower()
        if not _is_naming_token(word):
            continue
        if lowered in _ASK_WORDS or lowered in _GENERIC_DOCUMENT_WORDS:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(word)
    return out


def _document_text(doc) -> tuple[str, set[str]]:
    """One document as (normalized haystack, whole-word set).

    Title, source name and topics — never the summary. A summary is 600
    characters of the document's own vocabulary, so matching against it would
    make almost any term hit almost any document, which is ranking signal
    (Stage T's job, where the RPC already uses it) and the opposite of what
    identification needs.
    """
    title = getattr(doc, "title", "") or ""
    source = getattr(doc, "source_name", "") or ""
    topics = getattr(doc, "topics", None) or []
    joined = f"{title} {source} " + " ".join(str(t) for t in topics)
    return _norm(joined), _words(joined)


def _score_detail(terms: Sequence[str], doc) -> tuple[int, int]:
    """`(score, title_whole_word_hits)` for one document.

    The second value counts whole-word hits **against the TITLE ONLY**, and
    that restriction is the whole gate. Score may draw on the title, the
    source name and the topics, because all three are useful for RANKING. Only
    the title may establish that a message REFERS to a document, because the
    title is the document's name and the topics are not:

        catalog: "Q3 Pricing Teardown", topics ["pricing", "discounts"]
        message: "what's our pricing strategy for enterprise?"

    That message shares a topic word and names nothing. Counting topic hits
    toward the reference gate is what let it pin the teardown and be told to
    answer FROM it — a general strategy question deflected into one wiki page.
    Ranking on topics is right; REFERRING on them is not.
    """
    haystack, words = _document_text(doc)
    title_words = _words(getattr(doc, "title", "") or "")
    if not haystack:
        return 0, 0
    score = 0
    title_hits = 0
    # A "phrase" needs more than one word. Awarding the phrase bonus to a
    # single-term query would make a lone mid-word hit ("roadmap" inside
    # "Roadmapping Guidelines") score like an exact title match, which is the
    # substring guard defeated by arithmetic rather than by matching.
    if len(terms) >= 2:
        joined = _norm("".join(terms))
        if len(joined) >= _MIN_SUBSTRING_TERM and joined and joined in haystack:
            score += _PHRASE_SCORE
    for term in terms:
        token = _norm(term)
        if not token:
            continue
        if token in words:
            score += _WHOLE_WORD_SCORE          # whole word — any length
            if token in title_words:
                title_hits += 1
        elif len(token) >= _MIN_SUBSTRING_TERM and token in haystack:
            score += _SUBSTRING_SCORE           # mid-word, only if distinctive
    return score, title_hits


def score_document(terms: Sequence[str], doc) -> int:
    """How strongly `terms` name `doc`. 0 means not at all.

    Whole-word hits outweigh substrings, and a substring under
    `_MIN_SUBSTRING_TERM` characters scores nothing whatsoever — that floor is
    the "can" inside "Candidate" guard.
    """
    return _score_detail(terms, doc)[0]


def _rank(
    terms: Sequence[str], documents: Sequence[Any]
) -> list[tuple[int, int, Any]]:
    """Every document these terms touch, best first, as
    `(score, whole_word_hits, doc)`."""
    hits = [
        (score, whole, doc)
        for score, whole, doc in (
            (*_score_detail(terms, doc), doc) for doc in documents
        )
        if score > 0
    ]
    hits.sort(key=lambda triple: triple[0], reverse=True)
    return hits


def _pin(
    hits: list[tuple[int, int, Any]], basis: str, *, has_cue: bool
) -> DocumentReference:
    """Turn a ranking into one of THREE outcomes.

    The third one is the fix for a whole class of bug. This used to return
    only "pin" or "abstain", so once any document scored above zero — a single
    four-character substring against a topic was enough — the message was
    declared to REFER to a document, and an unresolvable reference became a
    user-visible "which document did you mean?". That produced clarifying
    questions on "can you summarize it?", on a first message with no thread at
    all (candidates empty, so the model could only ask a bare "which one?"),
    and on a message naming the single document in the workspace.

    So:

      NO REFERENCE   evidence too weak to claim the message points at a
                     document at all. Falls through to Stage T, silently, the
                     way every non-document question already does. This is the
                     common case and it must be quiet.
      ABSTAIN        evidence strong enough that the message clearly names
                     SOMETHING, and more than one document fits. Worth asking
                     about — this is the case the UNRESOLVED block exists for.
      PIN            one document, clearly.

    The reference gate is `_MIN_PIN_TITLE_WORDS` whole words matched against
    the TITLE, relaxed to one when the message carries a document cue. See
    `_DOCUMENT_CUE` for why length was the wrong signal.
    """
    if not hits:
        return DocumentReference()

    needed = 1 if has_cue else _MIN_PIN_TITLE_WORDS
    strong = [
        (score, title_hits, doc) for score, title_hits, doc in hits
        if title_hits >= needed and score >= _MIN_PIN_SCORE
    ]
    if not strong:
        # Words in common, but nothing that names a document. Not a reference.
        return DocumentReference()

    top_score, _, top_doc = strong[0]
    runner_up = strong[1][0] if len(strong) > 1 else 0
    if top_score - runner_up < _AMBIGUITY_MARGIN:
        tied = [
            doc for score, _, doc in strong
            if top_score - score < _AMBIGUITY_MARGIN
        ]
        if len(tied) < 2:
            # A lone qualifying document cannot be ambiguous with itself.
            return DocumentReference(
                documents=[top_doc], basis=basis, referenced=True,
            )
        return DocumentReference(
            referenced=True, abstained=True, basis=basis,
            reason=(
                "more than one document matches that reference equally well"
            ),
            candidates=tied[: MAX_RESOLVED_DOCUMENTS + 3],
        )
    return DocumentReference(
        documents=[top_doc], basis=basis, referenced=True,
    )


def has_document_anaphora(question: str) -> bool:
    """True when the message points at a document it does not name.

    Two shapes qualify, and the asymmetry is deliberate:

      * an EXPLICIT document anaphor — "summarize that page", "what's in the
        doc". It names a document kind, so it stands alone.
      * a BARE pronoun PLUS a reading cue — "what does it SAY about pricing?".
        A bare "it" is not evidence of anything on its own; pairing it with a
        verb about reading is what makes it a document reference.

    The pairing is what keeps "how many seats is it?" out. That message matches
    a pronoun and nothing else, and treating it as a document reference put an
    UNRESOLVED "which document do you mean?" section onto a question chat had
    understood perfectly.
    """
    text = question or ""
    if _EXPLICIT_DOC_ANAPHORA.search(text):
        return True
    return _has_bare_anaphora_about_a_document(text)


def _resolve_named(question: str, documents: Sequence[Any]) -> DocumentReference:
    """Resolve a document the MESSAGE ITSELF names, or abstain.

    Returns a not-referenced result when the message names nothing at all —
    distinct from abstaining, which means it named something unidentifiable.
    """
    terms = query_terms(question)
    if not terms:
        return DocumentReference()
    hits = _rank(terms, documents)
    if not hits:
        # The message carries naming words but none of them touch any document.
        # That is not a reference to a document we hold — it is a message about
        # something else, and claiming otherwise would be the overreach.
        return DocumentReference()
    return _pin(hits, "named", has_cue=bool(_DOCUMENT_CUE.search(question or "")))


def _titles_in_text(text: str, documents: Sequence[Any]) -> list[Any]:
    """Documents whose title appears in `text`, by whole-word containment.

    Used to read what an ASSISTANT turn established. Requires every word of the
    title to be present as a whole word, so a one-word overlap does not count
    a document as mentioned.
    """
    body = _words(text)
    if not body:
        return []
    found: list[Any] = []
    for doc in documents:
        title_words = _words(getattr(doc, "title", "") or "")
        # A single-word title is too weak a signal to establish a referent from
        # prose — "Pricing" appears in any answer about pricing.
        if len(title_words) < 2:
            continue
        if title_words <= body:
            found.append(doc)
    return found


def _without_current_turn(
    question: str, history: Optional[Sequence[dict]]
) -> list[dict]:
    """`history` with any trailing copy of the CURRENT message removed.

    THE THREAD MUST NOT CONTAIN THE MESSAGE IT IS BEING ASKED ABOUT. When it
    does, the backwards walk hits the current message first, re-resolves it
    through `_resolve_named`, and returns whatever the message's own words
    match — which is precisely what anaphora-first ordering exists to prevent.
    Executed: for "what does it say about discounts?" after a thread about the
    Q3 teardown, a duplicated turn pins a "Discount Policy" page instead.

    This is reachable today, not hypothetically. `ChatScreen.tsx` persists the
    user turn fire-and-forget (`void persistence.pushUserTurn(...)`) and then
    fires the ask; `routes.ask._load_history` reads the turns table with no
    ordering guarantee between the two. When the insert wins the race the
    current message is in `history`, intermittently.

    Fixed HERE rather than in the frontend on purpose: this function must be
    correct for every caller, and the frontend is only one of them — Slack and
    MCP entry points assemble history their own way, and a future one may
    legitimately include the current turn. A resolver that is only correct
    when its caller is well-behaved is the wrong shape for something whose
    entire job is refusing to guess. The frontend race is still worth closing
    on its own merits; this does not depend on it.
    """
    turns = list(history or [])
    target = (question or "").strip()
    while turns and (turns[-1].get("role") or "user").lower() == "user" \
            and (turns[-1].get("content") or "").strip() == target:
        turns.pop()
    return turns


def _established_referent(
    question: str, history: Optional[Sequence[dict]], documents: Sequence[Any]
) -> DocumentReference:
    """The document the THREAD has established, walking backwards.

    Most recent establishment wins, and the walk STOPS at the first turn that
    carries a document signal. Walking past an ambiguous turn to find an older
    unambiguous one would answer about a document two topics ago, which is the
    same confident-wrong-document failure from a different direction.

    Both roles are read. A user turn is re-resolved through the same named
    path, so every guard applies to it unchanged. An assistant turn is read
    for titles it mentions, and establishes a referent ONLY when exactly one
    appears: an answer that listed five documents has established nothing.
    """
    turns = list(history or [])[-_HISTORY_LOOKBACK_TURNS:]
    for turn in reversed(turns):
        role = (turn.get("role") or "user").lower()
        content = _clamp_turn(turn.get("content") or "")
        if not content:
            continue
        if role == "assistant":
            mentioned = _titles_in_text(content, documents)
            if len(mentioned) == 1:
                return DocumentReference(
                    documents=[mentioned[0]], basis="anaphoric", referenced=True,
                )
            if len(mentioned) > 1:
                return DocumentReference(
                    referenced=True, abstained=True, basis="anaphoric",
                    reason=(
                        "the previous answer covered more than one document, "
                        "so which one this follows up on is unclear"
                    ),
                    candidates=mentioned[: MAX_RESOLVED_DOCUMENTS + 3],
                )
            continue
        named = _resolve_named(content, documents)
        if named.referenced:
            # Carry the abstention forward too: an earlier turn we could not
            # resolve does not become resolvable by being older.
            return DocumentReference(
                documents=named.documents, basis="anaphoric", referenced=True,
                abstained=named.abstained, reason=named.reason,
                candidates=named.candidates,
            )
    # Nothing established. FALL BACK to the message's own words before giving
    # up: "what does the doc say about Q3 pricing?" carries a document cue AND
    # names its subject, and on a first message there is no thread to resolve
    # against. Without this it abstained with an EMPTY candidate list, so the
    # rendered block asked "which document do you mean?" and listed nothing —
    # the worst possible turn-one experience, and produced by the anaphora
    # branch claiming a message it could not serve.
    named = _resolve_named(question, documents)
    if named.referenced:
        return named
    # Genuinely nothing: no referent established, and the message names no
    # document either. Not a reference — stay quiet and let Stage T work.
    return DocumentReference()


def resolve_documents(
    question: str,
    documents: Sequence[Any],
    *,
    history: Optional[Sequence[dict]] = None,
) -> DocumentReference:
    """The document this message refers to, or a stated refusal to guess.

    `documents` are catalog rows the caller has ALREADY scoped to the company
    and the caller's own conversation (see the module docstring on tenancy);
    this function performs no I/O and no model call, so it costs an ask
    nothing beyond the rows it was handed.

    Resolution order — and each step is ahead of the next for a reason:

      0. An ANSWER to our own "which document?" question. Most specific signal
         there is: the user is replying to something we asked, about a list we
         showed them. Without this the abstention is a dead end (see
         `_answer_to_our_question`).
      1. An ANAPHORIC reference ("it", "that page") resolves against the
         thread, most recent establishment first.
      2. Otherwise a NAMED reference in the message's own words.
      3. None of those: `referenced=False`. The caller proceeds with topical
         selection exactly as before.

    Anaphora wins because when a message says "it", the words around it
    describe WHAT IS BEING ASKED, not WHICH DOCUMENT. In "what does it say
    about discounts?", `discounts` is the subject of the question — and
    resolving on it would pin a "Discount Policy" page over the teardown the
    user has been reading, which is the confidently-wrong-document failure in
    its purest form. The user already told us the referent is established;
    honouring that, or abstaining when nothing was established, is more
    faithful than overriding it with a topical word match.

    Naming still wins where it should: "actually, what about the security
    review?" carries no anaphor, so it takes branch 2 and switches documents.
    """
    if not documents:
        # Nothing to resolve against. Reported as no-reference rather than as
        # an abstention: a workspace with no catalogued documents has not
        # failed to identify one, and telling the model we could not pick
        # would imply there was something to pick from.
        return DocumentReference()

    history = _without_current_turn(question, history)
    answered = _answer_to_our_question(question, documents, history)
    if answered is not None:
        return answered

    # A message that NAMES a document strongly enough to pin outranks the
    # thread, even when it also carries an anaphor. "forget it — summarize the
    # Q4 board deck" contains "it", and treating that as the operative signal
    # returned one of the previous turn's candidates while the user was
    # plainly asking for something else.
    #
    # Safe only because the pin gate is strict: pinning needs two whole TITLE
    # words, or one plus a document cue. "what does it say about discounts?"
    # cannot pin — `discounts` matches a topic, not a title — so it still
    # falls to the thread, which is the ordering the module argues for and the
    # case it was written to protect.
    named = _resolve_named(question, documents)
    if named.documents:
        return named

    if has_document_anaphora(question):
        return _established_referent(question, history, documents)
    return named


def narrow_candidates(reply: str, candidates: Sequence[Any]) -> list[Any]:
    """Apply a user's reply to an earlier "which document?" abstention.

    Handles an ordinal ("the second one", "2") and any distinctive token from
    the options ("the Q4 one", "Q4", "teardown").

    Deliberately a LOOSER tokenizer than `query_terms`, and NOT subject to the
    pin gates. Both differences are the point: here we are choosing between
    two or three candidates the user has just been SHOWN, not identifying one
    among hundreds. `query_terms` strips document nouns because among hundreds
    they name nothing — but "the pricing page" vs "the pricing deck" differ by
    exactly those words. And "Q4" cannot pin a document from cold, yet it is a
    perfectly good answer to "did you mean Q3 or Q4?".
    """
    text = (reply or "").strip()
    if not text:
        return []

    # An ordinal, but only when the message is short enough to BE a selection.
    if len(text.split()) <= _MAX_SELECTION_WORDS:
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if word in _ORDINALS:
                index = _ORDINALS[word]
                if -len(candidates) <= index < len(candidates):
                    return [candidates[index]]

    terms = [
        _norm(w) for w in re.findall(r"[A-Za-z0-9]+", text)
        if _is_naming_token(w) and w.lower() not in _SELECTION_STOPWORDS
    ]
    if not terms:
        return []
    # Matched against the TITLE ALONE, not the haystack `_document_text`
    # builds. The user is choosing between options we RENDERED, and what we
    # rendered is `- {title} ({provider})` — so the words they can pick from
    # are the words they were shown.
    #
    # Found on real data: both of a workspace's two "Product requirements"
    # pages carry "template" in their TOPICS ("software development template",
    # "product requirements template") while only one has it in the title. So
    # "the template one" — an unambiguous choice between what was on screen —
    # matched both and re-asked, for a reason the user could not see. Topics
    # are the right signal for FINDING a document and the wrong one for
    # picking between documents already named.
    narrowed = [
        doc for doc in candidates
        if any(term and term in _norm(getattr(doc, "title", "") or "")
               for term in terms)
    ]
    return narrowed[:MAX_RESOLVED_DOCUMENTS]


def _offered_candidates(
    history: Optional[Sequence[dict]], documents: Sequence[Any]
) -> list[Any]:
    """The documents our own last answer OFFERED as a choice, or [].

    DETECTED FROM WHAT THE MODEL ACTUALLY WRITES, not from our prompt. The
    first version of this matched `UNRESOLVED_REFERENCE_HEADING` against the
    assistant turn — but that heading is rendered into the SYSTEM PROMPT, and
    an assistant turn holds the model's reply. Prompt rule 11 tells the model
    to name the possibilities and ask; it never asks it to echo the heading.
    So the condition could not occur in production, and step 0 was dead: we
    asked "did you mean Q3 or Q4?", the user answered, the cold path ran, "Q4"
    failed every guard, and we asked again — the exact dead end this step
    exists to close, hidden because the tests fabricated the heading as
    assistant content, a state production never produces.

    What rule 11 DOES reliably produce is an answer that names the candidate
    titles and asks a question. That is observable, so this keys on it: the
    last assistant turn mentions two or more catalog titles and asks something.

    Returns only the titles that turn actually named — never the whole
    catalog. The previous fallback (`prior.candidates or list(documents)`)
    turned "the second one" into an arbitrary catalog row, reported with
    `basis="clarified"` as though the user had chosen it.
    """
    turns = list(history or [])
    for i in range(len(turns) - 1, -1, -1):
        turn = turns[i]
        if (turn.get("role") or "user") != "assistant":
            continue
        content = _clamp_turn(turn.get("content") or "")
        if "?" not in content:
            return []
        mentioned = _titles_in_text(content, documents)
        return mentioned if len(mentioned) >= 2 else []
    return []


def _answer_to_our_question(
    question: str, documents: Sequence[Any], history: Optional[Sequence[dict]]
) -> Optional[DocumentReference]:
    """Read this message as the ANSWER to our own clarifying question.

    WITHOUT THIS, ABSTAINING IS A DEAD END — and a dead end is worse than not
    asking. Measured on the two-page collision: we ask "did you mean Q3 or
    Q4?", the user replies "the Q4 one", and every guard that makes the first
    abstention correct then fires again on the reply. "Q4" is two characters,
    so it cannot pin from cold; "the second one" survives no naming term at
    all. The user answers the question and gets asked it again.

    This is exactly the failure `call_index` records — a disambiguation that
    "poses a question it cannot read the answer to", where "both" fell through
    to the KG. Following that precedent means copying the FIX too, not only
    the guards.

    Candidates come from the titles our last answer actually NAMED
    (`_offered_candidates`) — never from the catalog at large, and never from
    re-resolving the original question. Both of those produced a document the
    user was never shown: "the second one" against a six-row catalog returned
    an arbitrary row, labelled `basis="clarified"` as though it had been
    chosen.

    Returns None when the last turn was not a clarifying question, so the
    caller proceeds normally.
    """
    candidates = _offered_candidates(history, documents)
    if len(candidates) < 2:
        return None

    # A reply that NAMES a document outright is a new request, not a
    # selection. "forget it - summarize the Q4 board deck" used to be narrowed
    # against the old options and return one of them; the user named a
    # different document and got a stale candidate. Cold resolution first, and
    # only fall back to narrowing when the reply names nothing on its own.
    named = _resolve_named(question, documents)
    if named.documents:
        return named

    picked = narrow_candidates(question, candidates)
    if len(picked) == 1:
        logger.info("document reference: clarification answered by the user")
        return DocumentReference(
            documents=picked, basis="clarified", referenced=True,
        )
    if not picked:
        # The reply chose nothing and named nothing — it has moved on. Let the
        # normal path handle it rather than re-asking about stale options.
        return None
    return DocumentReference(
        referenced=True, abstained=True, basis="clarified",
        reason="that reply still matches more than one of the documents offered",
        candidates=list(picked)[: MAX_RESOLVED_DOCUMENTS + 3],
    )


__all__ = [
    "DocumentReference",
    "MAX_RESOLVED_DOCUMENTS",
    "has_document_anaphora",
    "narrow_candidates",
    "query_terms",
    "resolve_documents",
    "score_document",
]
