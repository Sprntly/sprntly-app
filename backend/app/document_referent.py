"""Which document is the user talking about, when they never named it.

`ask_runner.document_grounding` already had two ways to put a document's body
in front of the model:

  * **Stage N** — the question spells the document's name out. Binary
    substring match, no tunable, and it is right by construction: naming a
    document is an unambiguous request for it.
  * **Stage T** — the catalog's fused lexical+semantic rank fills whatever
    slots Stage N left, deliberately with no floor. A wrong load there costs
    prompt budget and nothing else, because the loaded document is labelled
    as *automatically selected* and the model is told (prompts.py rule 6) to
    ignore the ones that do not bear on the question.

Neither can answer "what does **it** say about pricing?". Stage N has no name
to match. Stage T ranks on the word "pricing" alone and has no idea a document
was under discussion one turn ago. That gap is what this module closes.

WHAT THIS MODULE PRODUCES IS NOT ANOTHER WAY TO LOAD A DOCUMENT — Stage T
already loads. It produces a **referent**: an assertion, rendered into the
prompt, that *this* document is the thing the user's question is about. That
assertion is a much stronger claim than "here, this might be relevant", and it
is why the whole design is built around one property:

    A FALSE REFERENT IS WORSE THAN NO REFERENT.

Telling the model "the user is asking about the Q3 Pricing wiki page" when
they asked a plain business question makes the model answer *as that page*.
The previous attempt at this feature was rejected for exactly that: it pinned
a Confluence page onto "what's our pricing strategy?" because the question
contained a word of six characters or more. So there are three independent
guards here, and a question must clear all three:

  1. **A cue in the message.** The message must actually point at a document —
     a document noun ("the spec", "that deck", "our write-up"), a reading verb
     aimed at a source ("what does it say", "according to"), or an anaphor.
     `has_document_cue` is deterministic, runs first, and costs nothing. An
     ordinary business question fails here and never reaches an LLM.
  2. **A floor.** For the descriptive path, a candidate must share at least one
     content term with the question across its title, topics and summary.
     Cheap, visible, and — critically — a candidate that fails it is NOT
     denied: it stays exactly as eligible for Stage T loading as it was
     before. The floor gates the *assertion*, never the *evidence*, which is
     what makes a gate safe here when the identical gate was wrong in
     `_select_documents`.
  3. **An adjudicator that may answer "none".** One fast-model call, given the
     shortlist and the recent turns, whose entire job is to pick the referent
     *or say there isn't one*. It is told in as many words that a question
     about a business topic is not a reference to a document.

The floor deliberately does NOT use the RRF `score` the catalog returns. That
score is positional, not metric: `document_find_candidates`' semantic channel
ranks every embedded row by cosine distance with no cutoff, so the top
candidate scores ~1/51 + 1/51 whether it is a perfect match or the least-bad
row in an unrelated catalog. A threshold on it would look like a relevance
floor and be nothing of the kind. A real one needs the cosine distance (or the
lexical channel's hit flag) returned from the RPC — a schema change, noted for
the db-engineer, not smuggled in here as a magic constant.

The carry-forward path (`carried_referent`) needs no LLM at all and no
storage. There is no per-conversation pinned-document table and this module
does not want one: the referent is RE-DERIVED from the history that already
rides every prompt, by running the same binary name match Stage N uses over
the recent turns. Re-deriving cannot go stale, cannot leak across
conversations, and cannot disagree with what the model can see.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.graph.gateway import llm_call
from app.prompt_history import clamp_turn_text
from app.prompts import (
    DOCUMENT_AMBIGUOUS_HEADING as AMBIGUOUS_HEADING,
    DOCUMENT_REFERENT_HEADING as REFERENT_HEADING,
)

logger = logging.getLogger(__name__)

# ── Manifest labels ────────────────────────────────────────────────────────
#: The question referred back to a document a previous turn established.
MATCH_CARRIED = "carried"
#: The question described a document without naming it, and adjudication
#: identified which one.
MATCH_RESOLVED = "resolved"

# `REFERENT_HEADING` / `AMBIGUOUS_HEADING` are imported above from
# `app.prompts`, which is where they are defined and where the addendum rules
# that key off them live. One definition, both consumers — see the comment at
# their definition for the dead-guard bug this arrangement exists to make
# unwritable.

#: Turns of history scanned for a prior referent. Matches the depth a folded
#: history block realistically carries; a document named further back than
#: this is not something the model can see either, and asserting a referent
#: the model cannot corroborate is how a carry-forward turns into a fabrication.
HISTORY_TURNS_SCANNED = 8

#: Candidates put in front of the adjudicator. Small on purpose — the point is
#: a short, plausible shortlist, not a haystack; a long list is how "pick one"
#: turns into "pick something".
ADJUDICATION_CANDIDATES = 5

#: A carried title must be this substantial to be matched inside prose. A
#: one-word, five-character title ("Notes") substring-matches ordinary English
#: and would carry a referent off an accidental collision. Titles below the bar
#: simply do not carry — they are still nameable (Stage N) and still loadable
#: (Stage T); only the assertion is withheld.
MIN_CARRY_TITLE_CHARS = 8

ADJUDICATION_MODEL = "claude-haiku-4-5"
ADJUDICATION_PROMPT_VERSION = "document-referent-adjudication-v1"

_WORD_RE = re.compile(r"[a-z0-9]+")
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_SEPARATOR_RE = re.compile(r"[_\-.]+")

# Nouns that make a message point at a document. The exclusions are the
# interesting part of this list, and each one is a false positive that would
# otherwise be routine:
#   "report", "brief", "summary" — Sprntly has first-class Reports and a
#     first-class weekly Brief. "show me the report" asks for one of those.
#   "notes" — "what were the most recent release notes?" and "what came out of
#     the meeting notes" are topic questions far more often than they are
#     references to one catalogued document, and admitting the word bought a
#     model call on questions that were never going to resolve.
#   bare "drive" — "what drives churn" is not a Google Drive question.
_DOCUMENT_NOUNS = (
    r"(?:docs?|documents?|pages?|files?|specs?|write-?ups?|memos?|decks?"
    r"|pdfs?|one-?pagers?|agreements?|contracts?|proposals?|wikis?"
    r"|confluence|google\s+drive|gdrive)"
)

#: The same list MINUS bare "page", for the broad cue. Measured against 986
#: real user turns from the shared database: `page` matched 12 of them and
#: ELEVEN meant a UI screen — "branded error page", "the Settings page", "page
#: is sign in page", "web-rendered page". In a product-management tool, "page"
#: is overwhelmingly a thing you are building, not a thing you have written.
#:
#: It stays in `_DOCUMENT_NOUNS` above, so the ANAPHOR pattern still accepts
#: "what does that page say?" — a natural way to refer back to a wiki page.
#: That form appeared twice in the same 986 turns, and the anaphor path
#: additionally requires a prior turn to have named exactly one document, so
#: the residual risk is bounded in a way the bare cue's was not.
_CUE_NOUNS = (
    r"(?:docs?|documents?|files?|specs?|write-?ups?|memos?|decks?"
    r"|pdfs?|one-?pagers?|agreements?|contracts?|proposals?|wikis?"
    r"|confluence|google\s+drive|gdrive)"
)
_READING_VERBS = (
    r"(?:says?|said|saying|mentions?|mentioned|states?|stated|covers?|covered"
    r"|describes?|described|documented|wrote\s+up|written)"
)

#: Nouns strong enough that a determiner alone makes the phrase a reference:
#: "that doc", "the spec", "this deck". Nobody says "the spec" about a screen.
_STRONG_ANAPHOR_NOUNS = (
    r"(?:docs?|documents?|files?|specs?|write-?ups?|memos?|decks?"
    r"|pdfs?|one-?pagers?|agreements?|contracts?|proposals?|wikis?)"
)

#: Nouns that need a READING VERB before they count. Only "page" so far, and
#: the evidence is the same 986 real turns: "this page" appeared twice, once
#: meaning a wiki page and once meaning a login screen. A determiner cannot
#: separate those; a reading verb can. "what does that page SAY" is a
#: document reference; "users cannot see this page if they aren't logged in"
#: is not, and under the determiner-only rule it was one.
_WEAK_ANAPHOR_NOUNS = r"(?:pages?)"

#: Referring back at a document without naming it. Alternative 2 covers the
#: bare-verb forms — "does it say about X" contains "it say", and
#: `_READING_VERBS` admits both `say` and `says`. Alternative 3 is the weak
#: nouns, admitted only in front of a reading verb.
_ANAPHOR_RE = re.compile(
    rf"\b(?:that|this|the|those|these|it)\s+{_STRONG_ANAPHOR_NOUNS}\b"
    rf"|\b(?:it|that|this)\s+{_READING_VERBS}\b"
    rf"|\b(?:that|this|the|those|these)\s+{_WEAK_ANAPHOR_NOUNS}\s+{_READING_VERBS}\b",
    re.I,
)
#: Pointing at a document at all — by description, by anaphor, or by asking
#: what a source says. A message with none of this is not a document question,
#: and no amount of ranking may turn it into one.
_CUE_RE = re.compile(
    rf"\b{_CUE_NOUNS}\b"
    rf"|\b(?:according\s+to|as\s+per)\b"
    rf"|\bwhat\s+(?:does|did)\s+.{{0,40}}?\b{_READING_VERBS}\b",
    re.I,
)

# Small, closed stopword list for the content-term floor. Not a language
# model: the floor's job is to notice that a question and a document share no
# vocabulary at all, which is the only case where skipping adjudication is
# certainly right.
_STOPWORDS = frozenset("""
about above after again against all also am and any are because been before
being below between both but can cant could did does doing dont down during
each few for from further had has have having her here hers him his how
into its itself just more most much must my no nor not now off once only
other ought our ours out over own same she should so some such than that the
their theirs them then there these they this those through too under until
very was way we were what when where which while who whom why will with
would you your yours
""".split())


def _normalize(text: str) -> str:
    """Lowercase, drop a trailing file extension, turn `_ - .` into spaces,
    collapse whitespace. Deliberately the SAME shape as
    `ask_runner._normalize`, so a title that Stage N would have matched in the
    question is a title this module matches in the history — one rule, two
    places to apply it, no second notion of "the same document"."""
    stripped = _EXTENSION_RE.sub("", text or "")
    spaced = _SEPARATOR_RE.sub(" ", stripped)
    return " ".join(spaced.lower().split())


def _content_terms(text: str) -> set[str]:
    """Words long enough and common enough to mean something. Four characters
    is the floor because three-letter tokens in this domain ("API", "KPI") are
    real but also collide with everything."""
    return {
        w for w in _WORD_RE.findall((text or "").lower())
        if len(w) >= 4 and w not in _STOPWORDS
    }


@dataclass(frozen=True)
class KnownDocument:
    """One document the grounding block will actually RENDER this turn.

    `key` is the manifest's `file_id` — an upload's own id, or
    `{provider}:{external_id}` for a document in a connected system — so a
    resolved referent can be handed straight back to selection with no second
    lookup. Built from the post-truncation lists on purpose: a referent that
    is not in the rendered Index would put a body under "Contents loaded" for
    a document the Index does not list, which is the exact
    content-present/existence-missing inconsistency the index cap exists to
    prevent."""

    key: str
    title: str
    provider: str

    @property
    def title_norm(self) -> str:
        return _normalize(self.title)


@dataclass
class Resolution:
    """What resolution concluded. All three fields may be empty — "no referent
    and no ambiguity" is the expected outcome for most questions and is not a
    failure."""

    #: The document the question is about, or None.
    referent: Optional[KnownDocument] = None
    #: `carried` or `resolved`; None when there is no referent.
    how: Optional[str] = None
    #: Human-readable titles of the documents that tied. Non-empty ONLY when
    #: the question clearly pointed at a document and more than one could be
    #: meant — the case that must produce a question back to the user rather
    #: than a silent pick.
    ambiguous_titles: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.referent is not None


def has_document_cue(question: str) -> bool:
    """Does this message point at a document at all?

    Guard #1, and the one that carries the most weight, because it is
    deterministic and runs before anything expensive. "what's our pricing
    strategy?", "how big is the team?", "what should we build next?" all
    return False and end resolution right there — no ranking, no adjudicator,
    no referent, and Stage T's behaviour for them is byte-identical to what it
    was before this module existed."""
    text = question or ""
    return bool(_CUE_RE.search(text) or _ANAPHOR_RE.search(text))


def has_anaphor(question: str) -> bool:
    """Is this message referring BACK at a document rather than describing
    one? "what does it say about pricing", "does that doc cover Q3". This is
    what licenses looking at the conversation instead of only the message."""
    return bool(_ANAPHOR_RE.search(question or ""))


def carried_referent(
    question: str,
    history: Optional[list[dict]],
    known: Iterable[KnownDocument],
) -> Resolution:
    """The document a previous turn established, when this turn points back at
    it without naming it.

    Walks the history newest-first and stops at the FIRST turn that names any
    known document — not a union over the whole window. A conversation that
    discussed doc A five turns ago and doc B last turn is about doc B, and
    unioning them would report a false ambiguity for a thread that is
    perfectly clear.

    If that turn names exactly one document, it is the referent. If it names
    two or more, this returns them as `ambiguous_titles` and NO referent: two
    plausible documents must produce a question back to the user, never a
    silent pick. If no turn names anything, there is no referent — the
    question refers back to something, but nothing this module can see
    established it, and inventing one is precisely the failure mode this
    design exists to avoid.

    Both roles are scanned. An assistant turn that cited `[Source: X.pdf]`
    established X just as surely as the user naming it, and in practice more
    often. Each turn is passed through `clamp_turn_text` first, so the text
    searched here is the text the model actually sees folded into its prompt —
    a referent derived from a fragment the model was never shown would be
    unverifiable by the very model being told to rely on it."""
    if not has_anaphor(question):
        return Resolution()
    carryable = [
        doc for doc in known
        if len(doc.title_norm) >= MIN_CARRY_TITLE_CHARS
    ]
    if not carryable:
        return Resolution()
    turns = list(history or [])[-HISTORY_TURNS_SCANNED:]
    for turn in reversed(turns):
        text = clamp_turn_text(turn.get("content") if isinstance(turn, dict) else "")
        if not text:
            continue
        turn_norm = _normalize(text)
        named: list[KnownDocument] = []
        seen: set[str] = set()
        for doc in carryable:
            if doc.title_norm in turn_norm and doc.key not in seen:
                seen.add(doc.key)
                named.append(doc)
        if not named:
            continue
        if len(named) == 1:
            return Resolution(referent=named[0], how=MATCH_CARRIED)
        return Resolution(ambiguous_titles=[doc.title for doc in named])
    return Resolution()


def eligible_candidates(question: str, candidates: list[dict]) -> list[dict]:
    """Guard #2 — candidates that share at least one content term with the
    question, across title + topics + summary, capped at
    `ADJUDICATION_CANDIDATES`.

    This is a gate with a number in it, which `_select_documents` explicitly
    refuses to have, and the difference is worth being exact about. There, a
    gate DENIED: a document that failed it was never loaded and the user was
    told nothing relevant existed. Here, a document that fails is not denied
    anything — Stage T still ranks it, still loads it, still renders it in the
    Index with its summary. All it loses is the chance to be ASSERTED as the
    subject of the question. A miss costs a stronger prompt; it cannot cost an
    answer."""
    question_terms = _content_terms(question)
    if not question_terms:
        return []
    out: list[dict] = []
    for candidate in candidates:
        haystack = " ".join([
            str(candidate.get("title") or ""),
            " ".join(str(t) for t in (candidate.get("topics") or [])),
            str(candidate.get("summary") or ""),
        ])
        if question_terms & _content_terms(haystack):
            out.append(candidate)
        if len(out) >= ADJUDICATION_CANDIDATES:
            break
    return out


_ADJUDICATION_SYSTEM = """\
You decide WHICH document, if any, a user's message is talking about.

You are given the user's latest message, the last few turns of the \
conversation, and a numbered shortlist of documents (title, where it lives, a \
one-line summary, its topics). You never see the documents' full contents and \
you must not pretend to.

Answer with the number of the ONE document the message is about, or null.

Answer null — this is the expected answer, and you should reach for it \
readily — whenever any of these is true:
- The message asks about a business topic rather than about a document. \
"What's our pricing strategy?", "how big is the team?", "what should we build \
next?" are questions about the company. They are NOT references to a \
document, even when a document on the shortlist happens to be about the same \
subject. A shared subject is not a reference.
- The message points at a document but two or more of the shortlist could \
equally be it.
- The message points at a document that is not on the shortlist.
- You are not confident. Null is always the safe answer; guessing is not.

Answer with a number only when the message is POINTING AT a specific \
document — naming it, describing it ("our integration options write-up", "the \
security spec"), or referring back to one already under discussion — and \
exactly one shortlist entry is clearly that document.

Give a one-sentence reason for whichever answer you give."""

_ADJUDICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "document": {
            "type": ["integer", "null"],
            "description": (
                "The number of the shortlist entry the message is about, or "
                "null if the message is not about any one of them."
            ),
        },
        "reason": {"type": "string"},
    },
    "required": ["document", "reason"],
    "additionalProperties": False,
}


def _history_tail(history: Optional[list[dict]], turns: int = 4) -> str:
    lines: list[str] = []
    for turn in list(history or [])[-turns:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "user")
        text = clamp_turn_text(turn.get("content"), max_chars=600)
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def adjudicate(
    *,
    enterprise_id: str,
    question: str,
    candidates: list[dict],
    history: Optional[list[dict]] = None,
) -> Optional[str]:
    """Guard #3 — one fast-model call that names the referent or declines.

    Returns the chosen candidate's `external_id`, or None. Never raises: a
    gateway failure, a malformed answer, or an out-of-range index all mean "no
    referent", which degrades to exactly the behaviour that existed before
    this module — Stage T loads by rank and the prompt asserts nothing.

    An out-of-range or non-integer choice is treated as a refusal rather than
    clamped into the list. Clamping would turn the model's one way of saying
    "not this shortlist" into a pick, which is the failure this whole file is
    organised against."""
    if not candidates or not enterprise_id:
        return None
    shortlist = "\n".join(
        f"{i}. {c.get('title') or '(untitled)'} "
        f"[{c.get('source_name') or c.get('provider') or 'unknown source'}]"
        f"\n   summary: {(c.get('summary') or '(none)')[:400]}"
        f"\n   topics: {', '.join(str(t) for t in (c.get('topics') or [])) or '(none)'}"
        for i, c in enumerate(candidates, start=1)
    )
    tail = _history_tail(history)
    payload_input = (
        (f"RECENT CONVERSATION:\n{tail}\n\n" if tail else "")
        + f"USER'S LATEST MESSAGE:\n{question}\n\n"
        + f"SHORTLIST:\n{shortlist}"
    )
    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent="ask",
            purpose="document_referent",
            system=_ADJUDICATION_SYSTEM,
            input=payload_input,
            prompt_version=ADJUDICATION_PROMPT_VERSION,
            model=ADJUDICATION_MODEL,
            json_schema=_ADJUDICATION_SCHEMA,
            max_tokens=300,
        )
        payload = result.output if isinstance(result.output, dict) else {}
    except Exception:  # noqa: BLE001 — resolution must never break an answer
        logger.warning(
            "document referent adjudication failed for %s; "
            "answering with no referent", enterprise_id, exc_info=True,
        )
        return None

    choice = payload.get("document")
    if not isinstance(choice, int) or isinstance(choice, bool):
        return None
    if not (1 <= choice <= len(candidates)):
        return None
    return candidates[choice - 1].get("external_id") or None


def resolve(
    *,
    enterprise_id: str,
    question: str,
    history: Optional[list[dict]],
    known: list[KnownDocument],
    candidates: list[dict],
    by_external_id: dict[str, KnownDocument],
) -> Resolution:
    """The whole resolution decision, in the order the guards are cheapest.

    Called ONLY when Stage N selected nothing: a question that names a
    document has no implicit referent to work out, and resolution must never
    get to compete with an explicit name.

    Carry-forward is tried before description because it is both cheaper and
    stronger evidence — "what does it say" after a turn about one specific
    document is about as unambiguous as an implicit reference gets, and it
    needs no model call. Description falls through to adjudication.

    `by_external_id` maps a candidate's `external_id` back to the renderable
    document, so a candidate the Index does not list (an upload deleted since
    it was catalogued, a row below the index cap) resolves to nothing rather
    than to a body with no entry above it."""
    if not has_document_cue(question):
        return Resolution()

    carried = carried_referent(question, history, known)
    if carried.resolved or carried.ambiguous_titles:
        return carried

    shortlist = eligible_candidates(question, candidates)
    if not shortlist:
        return Resolution()
    external_id = adjudicate(
        enterprise_id=enterprise_id,
        question=question,
        candidates=shortlist,
        history=history,
    )
    if not external_id:
        return Resolution()
    document = by_external_id.get(external_id)
    if document is None:
        return Resolution()
    return Resolution(referent=document, how=MATCH_RESOLVED)


def render_referent_block(resolution: Resolution) -> list[str]:
    """The lines the documents block carries about the referent, or [].

    Two distinct outputs, never both:

    * A referent — states which document the question is about and stops.
      It does NOT tell the model the document was read; whether the body
      loaded is the Index's job to say, and a fetch that failed still says so
      there under its own marker. Asserting a subject and asserting a
      successful read are separate claims, and this block only makes the
      first.
    * An ambiguity — names the documents that tied and instructs the model to
      ask. This is the visible half of "does not silently pick one": without
      it, an ambiguous turn would be indistinguishable from a turn with no
      referent, and the model would quietly answer from whichever document
      Stage T happened to rank first."""
    if resolution.resolved:
        via = (
            "the user is referring back to it from an earlier turn in this "
            "conversation" if resolution.how == MATCH_CARRIED
            else "the user's message describes it"
        )
        return [
            "",
            f"## {REFERENT_HEADING}",
            f'"{resolution.referent.title}" — {via}. Read the question as '
            f"being about that document. This says which document is meant; "
            f"it does not say its contents were loaded — the Index above says "
            f"that.",
        ]
    if resolution.ambiguous_titles:
        titles = ", ".join(f'"{t}"' for t in resolution.ambiguous_titles)
        return [
            "",
            f"## {AMBIGUOUS_HEADING}",
            f"The question refers to a document, but more than one could be "
            f"meant: {titles}. Ask the user which one before answering from "
            f"any of them. Do not pick one yourself.",
        ]
    return []
