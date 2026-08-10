"""Background warmer for the predefined Ask prompts.

The home / Ask-Sprntly screens have a small fixed set of starter chips
(see `PREDEFINED_ASK_PROMPTS` in prompts.py). Each click sends the same
question text every time. By pre-generating responses at brief-creation
time we make those clicks render instantly — the route just returns a
cached row.
"""
import asyncio
import concurrent.futures as _futures
import contextvars
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app import document_referent
from app.corpus import load_corpus
from app.db import (
    complete_cached_ask,
    fail_cached_ask,
    find_cached_ask,
    start_cached_ask,
)
from app.document_bodies import BodyResolver
from app.document_catalog import (
    PROVIDER_CHAT_ATTACHMENT,
    PROVIDER_CONFLUENCE,
    PROVIDER_GOOGLE_DRIVE,
    PROVIDER_UPLOADS,
    find_candidates as find_catalog_candidates,
    list_documents as list_catalog_documents,
)
from app.document_sources import DocumentFileRef, get_file_text, list_company_files
from app.llm import DEFAULT_MODEL, LONG_REQUEST_TIMEOUT_S, call_json
from app.prompt_history import render_history_block
from app.usage_context import Feature, usage_scope
from app.prompts import (
    ASK_CACHE_VERSION,
    ASK_SYSTEM,
    ASK_SYSTEM_COMPANY_FACTS_ADDENDUM,
    ASK_SYSTEM_DOCUMENTS_ADDENDUM,
    ASK_SYSTEM_KG_ADDENDUM,
    ASK_SYSTEM_LIVE_SWEEP_ADDENDUM,
    connected_sources_line,
    today_line,
    ASK_USER_TEMPLATE_QUESTION_ONLY,
    ASK_USER_TEMPLATE_WITH_KG,
    PREDEFINED_ASK_PROMPTS,
)

logger = logging.getLogger(__name__)

# The header of the self-reported workspace-identity block injected into the
# answer prompt (see `company_facts_block` below). Deliberately NOT phrased as
# "authoritative" or "facts" alone — this is configuration of record (whatever
# the workspace typed into its own name/product/website fields), not
# independently verified truth; a customer's own typo renders as-is rather
# than being "corrected" toward a more plausible-looking value.
WORKSPACE_CONFIG_HEADER = "WORKSPACE CONFIGURATION (self-reported by this team)"

# Hostnames that are obviously infra, not a brand site — a preview-deploy
# domain, or localhost. The website is omitted rather than rendered when it
# resolves to one of these, or to an "app." subdomain, or carries a query
# string / a path deeper than the bare domain (see `_should_skip_website`).
_GUARDED_HOST_SUFFIXES = (".vercel.app",)


def _clean_text(value: str) -> str:
    """Trim and collapse internal whitespace (`"  Acme   Inc "` -> `"Acme Inc"`)."""
    return " ".join(value.split())


def _should_skip_website(url: str) -> bool:
    """True when `url` is obviously not the company's own brand site: a
    preview-deploy host (`*.vercel.app`), `localhost`, an "app." subdomain
    (the product itself, not the marketing site), or a URL carrying a query
    string or a path deeper than the bare domain. Fails safe: an unparseable
    or hostless value is also skipped rather than rendered."""
    try:
        parsed = urlparse(url if "://" in url else f"//{url}")
    except ValueError:
        return True
    host = (parsed.hostname or "").lower()
    if not host:
        return True
    if host == "localhost" or host.endswith(_GUARDED_HOST_SUFFIXES):
        return True
    if host.split(".")[0] == "app":
        return True
    if parsed.query:
        return True
    if parsed.path not in ("", "/"):
        return True
    return False


def company_facts_block(enterprise_id: str | None) -> str:
    """The workspace's self-reported identity — company name
    (`companies.display_name`) and its primary product's name/website
    (`products`, `is_primary` row) — rendered for the answer prompt as
    configuration of record, not verified fact (see `WORKSPACE_CONFIG_HEADER`).

    Returns "" for every degradation path — no tenant, no company row, no
    product row, every field empty/guarded, or any read failure — so chat
    behaves exactly as before for a tenant with no onboarding data yet."""
    if not enterprise_id:
        return ""
    try:
        from app.db.companies import display_name_for_company_id
        from app.db.products import get_primary_product

        company_name = _clean_text(display_name_for_company_id(enterprise_id) or "")
        product = get_primary_product(enterprise_id) or {}
    except Exception:  # noqa: BLE001 — grounding must never break an answer
        logger.warning(
            "workspace configuration unavailable for %s; answering without it",
            enterprise_id, exc_info=True,
        )
        return ""

    product_name = _clean_text(product.get("name") or "")
    website = (product.get("website") or "").strip()
    if website and _should_skip_website(website):
        website = ""

    lines: list[str] = []
    if company_name:
        lines.append(f"Company name: {company_name}")
    if product_name:
        lines.append(f"Product name: {product_name}")
    if website:
        lines.append(f"Website: {website}")
    if not lines:
        return ""
    return WORKSPACE_CONFIG_HEADER + "\n" + "\n".join(lines)


# ── Uploaded-document grounding (existence-vs-retrieval contract) ───────────
# Closes the incident where an uploaded document (stored in
# `document_source_file`, extraction succeeded) was reported as "not present
# in any connected source" because the ask path never read that table at
# all. Structural precedent: `company_facts_block` above — a per-tenant block
# computed once and composed into the cacheable user prefix.

DOCUMENT_INDEX_HEADER = "UPLOADED DOCUMENTS"

#: Chars per token, matching graph/retrieval.py's _CHARS_PER_TOKEN idiom —
#: size by serialized length, no tokenizer dependency.
_CHARS_PER_TOKEN = 4

#: Total budget for loaded document BODIES. Sized above the KG bundle's
#: DEFAULT_TOKEN_BUDGET (2200, graph/retrieval.py:41) because a document is
#: quoted, not summarized, and well under the 12000 max_tokens the answer
#: call already reserves.
DOCUMENT_TOKEN_BUDGET = 6000
_DOCUMENT_CHAR_BUDGET = DOCUMENT_TOKEN_BUDGET * _CHARS_PER_TOKEN  # 24000

#: At most this many documents load per question, however many match.
MAX_SELECTED_DOCUMENTS = 3

#: Index entries rendered before the list is visibly truncated.
MAX_INDEX_ENTRIES = 200

#: EVERY provider is selectable. There is deliberately no provider predicate
#: here any more: the previous one existed only because Drive files and
#: Confluence pages had no body reader, and both now resolve through
#: `document_bodies`. If a provider ever does need excluding, that belongs in
#: `document_find_candidates`, where the candidate window is COMPUTED — a
#: Python filter over the top-k the RPC already chose silently shrinks the
#: result set instead of asking for a different one, and starves selection
#: without saying so.
#:
#: What remains below is dispatch, not selection: uploads read from
#: `document_source_file`, chat attachments already reached the model through
#: their own turn's folded history, and everything else — today Drive and
#: Confluence, tomorrow whatever registers next — is read by
#: `document_bodies.BodyResolver`, which answers with a stated reason rather
#: than an exception when it does not know a source.

#: Providers whose documents are ALREADY represented in the index by another
#: read, and so must not also be rendered as connected-source entries.
#: `uploads` come from `list_company_files`; `chat_attachment` rows come from
#: the active conversation's turns.
_LOCALLY_INDEXED_PROVIDERS = frozenset({PROVIDER_UPLOADS, PROVIDER_CHAT_ATTACHMENT})

#: How a connected-source document names its origin in an index line. A
#: provider with no entry here renders under its own key rather than being
#: hidden — an unlabelled document is still a document that exists.
_PROVIDER_LABELS = {
    PROVIDER_GOOGLE_DRIVE: "Google Drive",
    PROVIDER_CONFLUENCE: "Confluence",
}

#: Candidates requested from the hybrid rank per question. Comfortably above
#: MAX_SELECTED_DOCUMENTS so rows that cannot be turned into a body (an
#: upload deleted since it was catalogued, a chat attachment already folded
#: into history) do not starve selection. A capacity number, not a relevance
#: one: raising it cannot change which document ranks first.
_CATALOG_CANDIDATE_K = 25

_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_SEPARATOR_RE = re.compile(r"[_\-.]+")


def _normalize(text: str) -> str:
    """Lowercase, drop a file extension, turn _ - . into spaces, collapse
    whitespace. 'Sprntly_vs_Productboard_Comparison.docx' ->
    'sprntly vs productboard comparison'."""
    stripped = _EXTENSION_RE.sub("", text)
    spaced = _SEPARATOR_RE.sub(" ", stripped)
    return " ".join(spaced.lower().split())


def _select_documents(
    question: str, refs: list[DocumentFileRef]
) -> list[DocumentFileRef]:
    """Stage N — the documents the question NAMES. Deterministic, pure, no
    I/O: a ref matches when its normalized filename stem is a substring of the
    normalized question, or its normalized source name is. Ranked by
    uploaded_at descending; keeps the first MAX_SELECTED_DOCUMENTS.

    Both arms are binary and have no tunable, which is why they survive
    unchanged. The third arm — filename-token overlap at a fixed ratio — is
    GONE, and is not replaced by another number. It was a relevance GATE: set
    too high (it was) a user asking about their document's topic silently got
    nothing, and every value is wrong for somebody's filename. Topical
    matching is now `_topical_candidates` below, where it is a RANK — ordered
    candidates filling the remaining slots, with no threshold to re-tune.

    Named matches load FIRST: naming a document is an unambiguous request for
    that document, and no ranking should be able to outrank it."""
    question_norm = _normalize(question)
    named: list[DocumentFileRef] = []
    for ref in refs:
        stem_norm = _normalize(ref.filename)
        source_norm = _normalize(ref.source_name) if ref.source_name else ""
        if (bool(stem_norm) and stem_norm in question_norm) or (
            bool(source_norm) and source_norm in question_norm
        ):
            named.append(ref)
    named.sort(key=lambda ref: ref.uploaded_at or "", reverse=True)
    return named[:MAX_SELECTED_DOCUMENTS]


def _topical_candidates(
    enterprise_id: str,
    question: str,
    *,
    question_embedding: list[float] | None,
    conversation_id: int | None,
    user_id: str | None,
    exclude_external_ids: set[str],
) -> list[dict]:
    """Stage T — the documents the question is ABOUT, by fused rank.

    Delegates to the catalog's `document_find_candidates`, which fuses a
    lexical channel (tsvector over title/source/summary/topics) and a semantic
    channel (cosine kNN over the summary embedding) by reciprocal rank fusion,
    and which holds the tenant filter and the conversation-ownership join
    inside its own body — there is no argument here that widens scope.

    Every provider the catalog holds is a candidate. Nothing is filtered out
    of the RPC's result here — see the note on `_LOCALLY_INDEXED_PROVIDERS`
    above for why a Python-side provider filter is the wrong shape and where
    one would belong instead.

    **There is no similarity floor and no score threshold.** RRF is rank-based
    by construction; the fusion constants are fusion shape, not a relevance
    knob. The consequence is deliberate and stated: whenever the catalog is
    non-empty, this returns candidates — including for questions no document
    is relevant to. Cost is bounded by the caps that already exist
    (MAX_SELECTED_DOCUMENTS documents, _DOCUMENT_CHAR_BUDGET chars), and the
    answer is kept honest by the prompt's ignore-if-irrelevant rule rather
    than by a score nobody can see. A wrong load costs budget; a wrong denial
    is the incident.

    `question_embedding` of None (unavailable, or all-zero — the accessor
    treats a zero vector as no vector, since a zero vector in cosine kNN ranks
    arbitrarily) drops the semantic channel and ranks on the lexical one
    alone: degraded, not dead. The caller records that degradation.

    Fails open to no candidates: discovery must never break an answer."""
    try:
        rows = find_catalog_candidates(
            enterprise_id,
            query=question,
            embedding=question_embedding,
            conversation_id=conversation_id,
            user_id=user_id,
            k=_CATALOG_CANDIDATE_K,
        )
    except Exception:  # noqa: BLE001 — discovery must never break an answer
        logger.warning(
            "document catalog candidate search failed for %s; "
            "topical selection contributes nothing this question",
            enterprise_id, exc_info=True,
        )
        return []

    out: list[dict] = []
    for position, row in enumerate(rows or [], start=1):
        external_id = row.get("external_id")
        if not external_id or external_id in exclude_external_ids:
            continue
        out.append({
            "provider": row.get("provider"),
            "external_id": external_id,
            "title": row.get("title") or "",
            "source_name": row.get("source_name") or "",
            "summary": row.get("summary") or "",
            "topics": list(row.get("topics") or []),
            "doc_date": row.get("doc_date"),
            "conversation_id": row.get("conversation_id"),
            "score": float(row.get("score") or 0.0),
            "rank": position,
        })

    # A conversation-scoped document outranks a workspace one ON EQUAL MATCH.
    # The SQL orders by fused score only, so this tie-break is stated here
    # rather than assumed: score first (unchanged ordering), then session
    # scope, then the rank the function returned. A document the user attached
    # to THIS conversation is the more likely referent of an ambiguous
    # question — but it does not get to jump a genuinely better match.
    out.sort(key=lambda c: (-c["score"], c["conversation_id"] is None, c["rank"]))
    return out


# ── Conversation-scoped attachments (chat attachments in the index) ─────────
# `qa_agent.answer()` -> `_answer_single_shot` has no `conversation_id` (or
# user identity) parameter, and adding one would mean four signature/call-site
# edits inside qa_agent.py — the file this fix is deliberately kept out of
# (see the ticket's Part C). `document_grounding` (called from BOTH grounding
# call sites — this module's `compose_ask_answer` and qa_agent.py's
# `_answer_single_shot`, neither of which changes) instead learns the active
# conversation, AND the identity of whoever is asking, from a pair of
# request-scoped ContextVars. `asyncio.to_thread` copies the current Context
# per call, so two Asks running concurrently in different worker threads never
# see each other's value.
#
# The user-id half exists ONLY because ownership here means company AND user
# — mirroring `routes.ask._load_history`'s per-user guard (a company-only
# check would still let one teammate's conversation_id pull another
# teammate's attachment text into their own prompt). `conversation_id` alone
# is genuinely unvalidated client input by the time it reaches this module —
# see `_owned_conversation_attachments` below.
_active_conversation_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "ask_runner_active_conversation_id", default=None
)
_active_conversation_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ask_runner_active_conversation_user_id", default=None
)


def set_active_conversation(conversation_id: int | None, user_id: str | None):
    """Record the conversation + caller identity for the Ask running on THIS
    thread's context. Call from `ask_job_runner._run_sync` immediately before
    `qa_agent.answer(...)`, and always undo via `reset_active_conversation` in
    a `finally` — a value left set past its own request would leak into
    whatever reuses that context next.

    Returns an opaque token, passed back to `reset_active_conversation`.
    """
    return (_active_conversation_id.set(conversation_id),
            _active_conversation_user_id.set(user_id))


def reset_active_conversation(tokens) -> None:
    """Undo `set_active_conversation` — call from a `finally`."""
    token_conversation, token_user = tokens
    _active_conversation_id.reset(token_conversation)
    _active_conversation_user_id.reset(token_user)


# ── The question embedding, by the same request-scoped route ────────────────
# Stage T's semantic channel was dead on the skill-routed path, which is the
# path most of this traffic actually takes: `qa_agent._answer_single_shot`
# calls `document_grounding(enterprise_id, question)` positionally, so
# `question_embedding` stayed at its default of None and the catalog search ran
# with `p_embedding => null`. Every candidate then scored on the lexical
# channel alone, and where that channel ties, the SQL's last-resort ordering is
# recency — so the newest document was returned for whatever was asked, and
# labelled a topic match.
#
# Giving `answer()` an embedding parameter would mean threading it through
# `answer() -> _answer_single_shot -> document_grounding`: the same four edits
# inside qa_agent.py that the conversation pair above exists to avoid. So the
# embedding rides the same request-scoped ContextVar route, set once per ask in
# `ask_job_runner._run_sync` and read by whichever grounding call site runs.
#
# ONE ContextVar holding a `(vector, degraded)` tuple rather than a pair of
# them, because "unset" and "set to None" mean different things here and must
# stay distinguishable: None-because-nobody-set-it lets a caller compute its
# own, whereas None-because-embedding-failed must NOT trigger a second attempt.
# The sentinel is the tuple's presence, not the vector's value.
_active_question_embedding: contextvars.ContextVar[
    tuple[list[float] | None, bool] | object | None
] = contextvars.ContextVar("ask_runner_active_question_embedding", default=None)


# A THIRD state for that slot: the ask has scoped it, but nothing has needed a
# vector yet. It has to stay distinguishable from the other two, which is why
# it is a sentinel object rather than another None:
#
#   * None             — nobody scoped it. A caller computes its own, as before.
#   * _EMBED_PENDING   — scoped but unresolved. Compute on FIRST USE and memoise
#                        the result back here, so the ask still pays for exactly
#                        one embedding no matter how many consumers ask for it.
#   * (vec, degraded)  — resolved. `(None, True)` means the attempt already
#                        FAILED and must never be retried within this ask.
#
# The eager embed this replaces was pure waste on the two commonest shapes. A
# tenant with no documents never reaches the topical stage that reads the
# vector — `document_grounding` returns at its `not refs and not
# conv_attachments and not connected` branch first — and a PRD-grounded ask
# skips KG retrieval altogether. Both paid a full embedding round trip for a
# vector no consumer ever read (measured at 2.8s on a doc-less tenant).
_EMBED_PENDING: object = object()


# ── The documents the PLANNER named, by the same request-scoped route ────────
# `document_grounding` is reached positionally through
# `qa_agent._answer_single_shot`, so a plan field cannot be threaded to it
# without the four edits inside qa_agent.py that this whole mechanism exists to
# avoid. Same route as the conversation pair, the embedding and the history.
_active_planned_documents: contextvars.ContextVar[
    list[str] | None
] = contextvars.ContextVar("ask_runner_active_planned_documents", default=None)


def set_active_planned_documents(external_ids: list[str] | None):
    """Record the catalog ids THIS ask's plan named. Always undo in a
    `finally`, exactly like the setters beside it."""
    return _active_planned_documents.set(list(external_ids or []))


def reset_active_planned_documents(token) -> None:
    """Undo `set_active_planned_documents` — call from a `finally`."""
    _active_planned_documents.reset(token)


def _carried_planned_documents() -> list[str]:
    """Catalog ids the plan named, or [] when nothing set any."""
    return _active_planned_documents.get() or []


def set_active_question_embedding_pending():
    """Scope this ask's embedding slot WITHOUT computing the vector.

    The counterpart to `set_active_question_embedding` for the worker path:
    it reserves the slot so `_resolve_question_embedding` knows it is allowed
    to memoise into it, but issues no HTTP call. Whichever consumer needs a
    vector first pays for it; consumers that never need one pay nothing.

    Returns an opaque token for `reset_active_question_embedding` — the same
    token discipline as the eager setter, so the caller's `finally` is
    unchanged. `ContextVar.reset` restores whatever was there before THIS set,
    so a value memoised into the slot afterwards is still correctly unwound."""
    return _active_question_embedding.set(_EMBED_PENDING)


def set_active_question_embedding(embedding: list[float] | None, degraded: bool):
    """Record THIS ask's question embedding for every consumer on this thread's
    context. Call from `ask_job_runner._run_sync` beside
    `set_active_conversation`, and always undo in the same `finally`.

    `degraded` is carried alongside so the consumer that logs can say WHY the
    semantic channel was absent — a failed embedding and a caller that simply
    had no vector look identical at the point of use.

    Returns an opaque token for `reset_active_question_embedding`."""
    return _active_question_embedding.set((embedding, degraded))


def reset_active_question_embedding(token) -> None:
    """Undo `set_active_question_embedding` — call from a `finally`."""
    _active_question_embedding.reset(token)


def _carried_question_embedding() -> tuple[list[float] | None, bool] | None:
    """This ask's already-computed `(vector, degraded)`, or None if nothing set
    one. Never computes — see `_resolve_question_embedding`.

    A slot left at `_EMBED_PENDING` reads as None here on purpose: to a caller
    that only wants an already-resolved vector, "scoped but not yet computed"
    and "nobody scoped one" are the same answer. Only
    `_resolve_question_embedding` distinguishes them, because only it is
    allowed to do the work and write the result back."""
    carried = _active_question_embedding.get()
    if carried is _EMBED_PENDING:
        return None
    return carried  # type: ignore[return-value]


# ── The conversation history, by the same request-scoped route ──────────────
# Document RESOLUTION (app.document_referent) needs the prior turns: "what does
# it say about pricing?" has no name for Stage N to match and no useful topic
# for Stage T to rank — the only thing that can say what "it" is, is the turn
# that named a document. `routes/ask.py::_load_history` already loads exactly
# that, and `ask_job_runner._run_sync` already holds it as a parameter, so
# nothing new is fetched here.
#
# It rides a ContextVar for the same reason the conversation pair and the
# embedding above do: `qa_agent._answer_single_shot` calls
# `document_grounding(enterprise_id, question)` positionally, and threading a
# fourth value through `answer() -> _answer_single_shot -> document_grounding`
# is the set of edits inside qa_agent.py this mechanism exists to avoid.
#
# CRITICAL, and separately tested: history reaches RESOLUTION only. It is never
# folded into `question`, which both grounding call sites still pass bare —
# `_select_documents`' substring rule against a question with three turns of
# prose glued onto it would match half the workspace's filenames at once.
_active_history: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar(
    "ask_runner_active_history", default=None
)


def set_active_history(history: list[dict] | None):
    """Record THIS ask's conversation history for document resolution. Call
    from `ask_job_runner._run_sync` beside `set_active_conversation`, and
    always undo in the same `finally` — a worker thread that kept a previous
    ask's history would resolve one user's pronoun against another user's
    conversation.

    Returns an opaque token for `reset_active_history`."""
    return _active_history.set(history)


def reset_active_history(token) -> None:
    """Undo `set_active_history` — call from a `finally`."""
    _active_history.reset(token)


def _owned_conversation_attachments(
    enterprise_id: str, conversation_id: int | None, caller_user_id: str | None
) -> list[tuple[int, int, dict, str]]:
    """(turn_id, index, attachment, turn_created_at) for every attachment on
    every turn of `conversation_id` — but ONLY when that conversation belongs
    to `enterprise_id` AND to `caller_user_id`, mirroring
    `routes.ask._load_history`'s per-user ownership guard.

    `conversation_id` reaching this function is genuinely unvalidated: it
    rides `ask_job_runner._run_sync`'s local variable straight from
    `AskIn.conversation_id`, with no ownership check anywhere on that path —
    `routes.ask._load_history` checks ownership too, but only to decide what
    goes into `history`; it does not gate what `conversation_id` itself gets
    passed onward. A query filtered on `conversation_id` alone would be an
    IDOR: any caller could supply another company's or another teammate's
    real conversation id and pull its private attachment text into their own
    prompt. Failing the check — wrong company, wrong user, no id, or any read
    error — returns [] silently, exactly as if no conversation_id had been
    passed at all."""
    if not conversation_id or not caller_user_id:
        return []
    try:
        from app.db.client import require_client

        c = require_client()
        owned = (
            c.table("conversations")
            .select("id")
            .eq("id", conversation_id)
            .eq("company_id", enterprise_id)
            .eq("user_id", caller_user_id)
            .limit(1)
            .execute()
        )
        if not owned.data:
            return []
        turns = (
            c.table("conversation_turns")
            .select("id,attachments,created_at")
            .eq("conversation_id", conversation_id)
            .order("created_at")
            .execute()
        )
    except Exception:  # noqa: BLE001 — grounding must never break an answer
        logger.warning(
            "conversation attachment index read failed for conversation=%s; "
            "treating as no attachments", conversation_id, exc_info=True,
        )
        return []
    out: list[tuple[int, int, dict, str]] = []
    for turn in turns.data or []:
        for index, attachment in enumerate(turn.get("attachments") or []):
            if not attachment.get("name"):
                continue
            out.append((turn["id"], index, attachment, turn.get("created_at") or ""))
    return out


def active_conversation_attachment_names(enterprise_id: str) -> list[str]:
    """Filenames only — never bodies — for every attachment on the ACTIVE
    conversation (the request-scoped ContextVar pair set by
    `ask_job_runner._run_sync` via `set_active_conversation`).

    A DELIBERATE DUPLICATE of `_owned_conversation_attachments`'s two-query
    ownership check (conversation belongs to `enterprise_id` AND to the
    caller), not a call into it: that function's return shape carries full
    attachment dicts (including extracted body text), and this one exists
    specifically so a caller building a ROUTING string (qa_agent's filename
    augmentation, see the module docstring above) never has a body anywhere
    in scope to leak — even transiently in a local variable one refactor away
    from being included. A filename like "Sprint Planning Board.docx" is
    already enough signal for the router; the body is exactly the thing that
    hijacked routing before this existed.

    Best-effort like every other read here: no active conversation, failed
    ownership, or any read error → []."""
    conversation_id = _active_conversation_id.get()
    caller_user_id = _active_conversation_user_id.get()
    if not conversation_id or not caller_user_id:
        return []
    try:
        from app.db.client import require_client

        c = require_client()
        owned = (
            c.table("conversations")
            .select("id")
            .eq("id", conversation_id)
            .eq("company_id", enterprise_id)
            .eq("user_id", caller_user_id)
            .limit(1)
            .execute()
        )
        if not owned.data:
            return []
        turns = (
            c.table("conversation_turns")
            .select("attachments")
            .eq("conversation_id", conversation_id)
            .execute()
        )
    except Exception:  # noqa: BLE001 — routing must never break the answer
        logger.warning(
            "conversation attachment name read failed for conversation=%s; "
            "treating as no attachments", conversation_id, exc_info=True,
        )
        return []
    names: list[str] = []
    for turn in turns.data or []:
        for attachment in turn.get("attachments") or []:
            name = attachment.get("name")
            if name:
                names.append(name)
    return names


def _catalog_documents(
    enterprise_id: str, conversation_id: int | None, user_id: str | None
) -> list:
    """Every catalog row this caller may see. [] on any read failure.

    Serves two jobs at once, which is why it returns the rows rather than a
    map. It ENRICHES the index lines the uploads/attachment reads already
    produced — for those, the catalog is not the index's spine, so a catalog
    that is empty, stale or unreadable costs summaries and topics, never
    existence. And it SUPPLIES the entries for documents that live in a
    connected system, which have no other read: a Confluence page or a Drive
    file is in this workspace's index only because the catalog says so.

    That asymmetry is worth stating plainly, because it is a real reduction in
    robustness for those two providers: a catalog outage downgrades an upload
    to a summary-less line, but makes a connected-source document invisible.
    It is still strictly better than the alternative it replaces, which was
    that they were never in the index at all."""
    try:
        return list_catalog_documents(
            enterprise_id, conversation_id=conversation_id, user_id=user_id
        )
    except Exception:  # noqa: BLE001 — enrichment must never break an answer
        logger.warning(
            "document catalog read failed for %s; index renders without "
            "summaries and without connected-source documents",
            enterprise_id, exc_info=True,
        )
        return []


def _connected_source_head(doc) -> str:
    """The `- {name} ({where}, updated {date})` head for a document that lives
    in a connected system rather than in this workspace's uploads.

    Deliberately NOT phrased like an upload. The prompt's existence rules key
    off the difference: the model must be able to say "that is a Confluence
    page in the SD space", not describe a wiki page as something the workspace
    uploaded."""
    label = _PROVIDER_LABELS.get(doc.provider, doc.provider)
    source_name = (doc.source_name or "").strip()
    where = (
        f"{label}: {source_name}"
        if source_name and source_name != label
        else label
    )
    date = (doc.doc_date or doc.updated_at or "")[:10]
    head = f"- {doc.title} ({where}"
    return f"{head}, updated {date})" if date else f"{head})"


def _index_line(
    head: str, catalog_doc, loaded: bool, partial_index: bool,
    unavailable_reason: str = "",
) -> str:
    """One index entry: the existing `- {name} ({scope}, {date})` head, then
    the catalog's one-line summary, then its topics, then whether the body is
    loaded for THIS question.

    The summary is the routing hint that lets the model notice a selection
    mistake — "the loaded documents don't cover this, but X looks relevant" —
    which is the point of showing it. The loaded-marker is what stops the
    model treating a one-line summary as if it had read the document.

    `unavailable_reason` is the THIRD state, and the one this whole line of
    work exists for: a document that WAS selected and whose contents could not
    be fetched. It is not "not loaded" (nobody asked for it) and it is
    emphatically not absent — the entry is right there in the index. The
    marker says so in words the model can repeat to the user without turning a
    fetch failure into a denial that the document exists."""
    parts = [head]
    if catalog_doc is not None:
        summary = (catalog_doc.summary or "").strip()
        if summary:
            parts.append(f"— {summary}")
        topics = [t for t in (catalog_doc.topics or []) if t]
        if topics:
            parts.append(f"Topics: {', '.join(topics)}.")
    if unavailable_reason:
        parts.append(
            f"[this document exists, but its contents could not be loaded for "
            f"this question: {unavailable_reason}]"
        )
    else:
        parts.append(
            "[loaded for this question]" if loaded
            else "[not loaded for this question]"
        )
    return " ".join(parts)


@dataclass
class _Chosen:
    """One document selection has picked, whatever it is stored in.

    `key` is the manifest's `file_id` — an upload's own id, the synthetic
    `turn:{id}:attachment:{i}` for a chat attachment, or `{provider}:{id}`
    for a document that lives in a connected system. `name` is what the model
    is told to cite, and must match the Index line exactly."""

    key: str
    name: str
    provider: str
    ref: DocumentFileRef | None = None
    catalog_doc: object | None = None


def document_grounding(
    enterprise_id: str | None,
    question: str,
    conversation_id: int | None = None,
    *,
    question_embedding: list[float] | None = None,
    history: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """Render the "UPLOADED DOCUMENTS" block (index of every uploaded file,
    plus the bodies selected for this question) and its server-derived
    manifest, for composition into the answer prompt.

    `conversation_id`, when it resolves (either passed explicitly, or — when
    left at its default `None` — read from the active-conversation ContextVar
    set by `ask_job_runner._run_sync`, see `set_active_conversation` above)
    AND passes the ownership check in `_owned_conversation_attachments`,
    additionally folds that ONE conversation's chat attachments into the
    index as entries reading "attached to this conversation" — distinct from
    workspace-upload entries, which read "source: {name}" — and into the
    manifest, each carrying the synthetic id
    `f"turn:{turn_id}:attachment:{index}"` (`TurnAttachment` has no id field
    of its own). A conversation_id that fails ownership — wrong company,
    wrong user, unset, or any read error — behaves exactly as if it had never
    been passed: no attachment entries, no error.

    Selection runs in three stages. **Stage N** loads the documents the
    question NAMES (`_select_documents`, substring match, binary, no tunable).
    **Stage R** — only when Stage N found nothing — works out which document
    the question is ABOUT WITHOUT naming it, from the message and from the
    conversation (`app.document_referent`). **Stage T** fills whatever slots
    remain with the documents the question is topically about, by the
    catalog's fused lexical+semantic rank (`_topical_candidates`, no floor, no
    threshold). Named beats resolved beats topical, always.

    Stage R is the only stage that ASSERTS anything. Stages N and T put a
    body in the prompt and leave the model to judge relevance (prompts rule
    6); Stage R additionally renders a line saying *this* document is what the
    question is about, which is a claim strong enough that a wrong one makes
    the model answer as the wrong document. Its guards, and why a relevance
    gate is right there and wrong in `_select_documents`, are in
    `app.document_referent`'s module docstring. Its two failure modes are both
    visible: no referent renders nothing extra and behaves exactly as this
    function did before it existed, and an ambiguous referent renders a block
    telling the model to ask which document is meant rather than pick.

    `history` — this conversation's prior turns, oldest first, in
    `routes.ask._load_history`'s `[{role, content}]` shape — is what lets
    Stage R resolve "what does it say about pricing?" to the document a
    previous turn established. Left at None it resolves from the ContextVar
    `ask_job_runner._run_sync` sets, exactly as `conversation_id` and
    `question_embedding` do. History is used ONLY by Stage R: Stages N and T
    still see the bare question, never the thread.

    ALL THREE STAGES COVER EVERY PROVIDER. Documents that live in a connected
    system — Confluence pages, Drive files — are indexed from the catalog and
    their bodies resolve through `document_bodies`: Drive from the corpus
    markdown its sync already wrote, Confluence by fetching the page live.
    Until that existed those two were reachable only by NAMING them, which is
    the same defect, one layer up, as needing to spell a filename: a user who
    asked what their wiki said about a topic got nothing, and was told to go
    ask about Confluence specifically.

    A connected-source document whose body cannot be fetched stays in the
    index with its summary and a marker saying its contents could not be
    loaded AND WHY. It must never read as absence — "we could not reach
    Confluence" and "you have no such page" are different sentences, and
    collapsing them is the incident this exists to close.

    `question_embedding` is computed ONCE per ask by the caller and threaded
    in, because this function runs BEFORE knowledge-graph retrieval and on
    PRD-grounded asks that retrieval never runs at all — so there is no
    existing embedding here to reuse. When it is left at its default — which
    is what `qa_agent._answer_single_shot`'s positional call does, and that is
    the path most traffic takes — it resolves from the request-scoped
    ContextVar `ask_job_runner._run_sync` sets, exactly as `conversation_id`
    above does. This function NEVER embeds on its own behalf: it uses what it
    was handed or what the ask already computed, so an ask can only ever pay
    for one embedding however many grounding calls it makes.

    Passing None with no ContextVar set is still legitimate and means Stage T
    ranks lexically only (see `_topical_candidates`) — but it now means a
    caller genuinely had no vector, rather than a call site forgetting to pass
    one.

    Returns ("", []) for every degradation path — no tenant, nothing to show
    (no uploaded files AND no conversation attachments), or any read failure
    — so composition is byte-identical to today for a tenant with no uploads
    and no attached-in-chat documents."""
    if not enterprise_id:
        return "", []
    try:
        refs = list_company_files(enterprise_id)
    except Exception:  # noqa: BLE001 — grounding must never break an answer
        logger.warning(
            "document index unavailable for %s; answering without it",
            enterprise_id, exc_info=True,
        )
        return "", []

    # Same request-scoped route as the conversation pair below it. `degraded`
    # is only meaningful when the ask computed the vector, so a caller that
    # threaded one in explicitly is recorded as not-degraded.
    embedding_degraded = False
    if question_embedding is None:
        carried = _carried_question_embedding()
        if carried is not None:
            question_embedding, embedding_degraded = carried

    if conversation_id is None:
        conversation_id = _active_conversation_id.get()
    if history is None:
        history = _active_history.get()
    caller_user_id = _active_conversation_user_id.get()
    conv_attachments = _owned_conversation_attachments(
        enterprise_id, conversation_id, caller_user_id
    )

    catalog_docs = _catalog_documents(
        enterprise_id, conversation_id, caller_user_id
    )
    catalog = {doc.external_id: doc for doc in catalog_docs}

    # Documents that live in a connected system. `uploads` and
    # `chat_attachment` rows are dropped here NOT as a selection filter but
    # because they are already in the index by another route, and listing them
    # twice would tell the model the workspace holds two copies of one file.
    connected = [
        doc for doc in catalog_docs
        if doc.provider not in _LOCALLY_INDEXED_PROVIDERS
    ]
    connected.sort(key=lambda d: (d.doc_date or d.updated_at or ""), reverse=True)

    if not refs and not conv_attachments and not connected:
        # Nothing to show — recorded rather than silent, so "this workspace has
        # no documents" stays distinguishable from "selection found none of
        # the documents it has" (AC7's two cases look identical in an answer).
        _log_document_selection(
            enterprise_id,
            manifest=[],
            catalog_size=len(catalog_docs),
            catalog_by_provider=_catalog_by_provider(catalog_docs),
            topical_ran=False,
            topical_candidates=0,
            question_embedding=question_embedding,
            embedding_degraded=embedding_degraded,
            index_empty=True,
        )
        return "", []

    total = len(refs) + len(connected)
    truncated_index = total > MAX_INDEX_ENTRIES
    # Truncate BEFORE selecting: if selection ran against the untruncated
    # list, a matched document ranked outside the visible index could have
    # its body rendered under "Contents loaded" while being absent from both
    # the rendered Index and the manifest — reproducing, inside a
    # >MAX_INDEX_ENTRIES tenant, the exact content-present/existence-missing
    # inconsistency this ticket exists to eliminate.
    #
    # Uploads and connected-source documents share ONE cap rather than getting
    # one each, so connecting a wiki cannot grow the worst-case prompt: the
    # ceiling on rendered entries is exactly what it was before. Uploads fill
    # it first, which means a workspace already at the cap on uploads alone
    # sees no connected-source entries — visibly, via the PARTIAL marker,
    # rather than silently.
    refs = refs[:MAX_INDEX_ENTRIES]
    connected = connected[:max(0, MAX_INDEX_ENTRIES - len(refs))]

    def _connected_key(doc) -> str:
        return f"{doc.provider}:{doc.external_id}"

    connected_by_external_id = {doc.external_id: doc for doc in connected}

    # ── Stage N: the documents the question NAMES. These load first, and a
    #    ranking never displaces them.
    selected: list[_Chosen] = [
        _Chosen(key=ref.id, name=ref.filename, provider=PROVIDER_UPLOADS, ref=ref)
        for ref in _select_documents(question, refs)
    ]
    # Stage N over the catalog too, so naming a wiki page or a Drive file
    # lands the same way naming an upload does. Same binary substring rule,
    # same no-tunable: a title the question spells out is an unambiguous
    # request for that document whatever system it happens to live in.
    named_keys = {chosen.key for chosen in selected}
    question_norm = _normalize(question)
    for doc in connected:
        if len(selected) >= MAX_SELECTED_DOCUMENTS:
            break
        title_norm = _normalize(doc.title)
        if not title_norm or title_norm not in question_norm:
            continue
        key = _connected_key(doc)
        if key in named_keys:
            continue
        named_keys.add(key)
        selected.append(_Chosen(
            key=key, name=doc.title, provider=doc.provider, catalog_doc=doc
        ))
    selected = selected[:MAX_SELECTED_DOCUMENTS]
    match_by_id: dict[str, tuple[str, int | None]] = {
        chosen.key: ("named", None) for chosen in selected
    }

    # ── Stage P: documents the PLANNER named.
    #
    # The planner is shown the catalog (ids, titles, one-line summaries) and can
    # say which document a question is about. It sees the whole thread and the
    # inlined attachment text, so it resolves the deictic case Stage N cannot —
    # "what does it say about pricing?" has no title to substring-match.
    #
    # AFTER Stage N, never before: a title the user spelled out is an
    # unambiguous request and a model's opinion must not displace it.
    #
    # LABELLED "topic", NOT "named", and that is the safety property rather than
    # an oversight. `document_referent` exists because an earlier attempt at
    # exactly this pinned a Confluence page onto "what's our pricing strategy?"
    # and the model answered AS that page; its rule is that a FALSE REFERENT IS
    # WORSE THAN NO REFERENT. "named" asserts the user asked for this document.
    # "topic" says it was selected automatically — which is the honest claim for
    # a model's pick, and which prompts.py rule 6 already tells the answering
    # model it may ignore. So a wrong pick here costs prompt budget, exactly
    # like a wrong Stage T pick, instead of hijacking the answer's voice.
    #
    # Filling slots here is also what lets Stage T's fused-rank RPC be skipped
    # entirely (`topical_ran` below is `len(selected) < MAX_SELECTED_DOCUMENTS`)
    # — measured at 8.67s on a live ask, and the single slowest query in it.
    for _planned_id in _carried_planned_documents():
        if len(selected) >= MAX_SELECTED_DOCUMENTS:
            break
        _doc = connected_by_external_id.get(_planned_id)
        if _doc is None:
            continue  # not visible to this caller — the gate already tenant-scoped it
        _key = _connected_key(_doc)
        if _key in match_by_id:
            continue
        selected.append(_Chosen(
            key=_key, name=_doc.title, provider=_doc.provider, catalog_doc=_doc
        ))
        match_by_id[_key] = ("topic", None)

    # ── Stage T: fill whatever slots remain by fused rank.
    #
    # `match: "topic"` is set ONLY inside the loop below, once per candidate
    # the fused rank actually returned. So a question that ranks nothing —
    # both channels empty — produces no topic labels at all, and selection
    # ends with whatever Stage N named. There is deliberately no fallback
    # here: ordering the catalog by recency and calling the head of that list
    # a topic match is how "the newest document, whatever you asked" came to
    # be presented as a ranked answer, and it must not be reachable.
    by_ref_id = {ref.id: ref for ref in refs}
    topical_ran = len(selected) < MAX_SELECTED_DOCUMENTS
    topical_candidates: list[dict] = []
    if topical_ran:
        # Stage T's semantic channel is the first thing in an ask that actually
        # READS the vector, so this is where the ask pays for it. Nothing above
        # needs one — the index read, the ownership checks and Stage N's
        # substring match are all lexical — and the `not refs and not
        # conv_attachments and not connected` return further up means a
        # workspace with no documents at all exits before reaching here and now
        # embeds nothing whatsoever.
        #
        # Guarded on `is None` so a caller that threaded its own vector in still
        # wins, and `embedding_degraded` keeps its documented meaning: it is set
        # only on the branch where this ask computed the vector itself.
        if question_embedding is None:
            question_embedding, embedding_degraded = _resolve_question_embedding(
                enterprise_id, question
            )
        topical_candidates = _topical_candidates(
            enterprise_id,
            question,
            question_embedding=question_embedding,
            conversation_id=conversation_id,
            user_id=caller_user_id,
            exclude_external_ids=set(match_by_id),
        )

    # ── Stage R: WHICH document is this question about, when it named none?
    #
    # Runs between the two existing stages and only when Stage N is empty —
    # a question that spelled a document's name out has no implicit referent
    # to work out, and resolution must never get to argue with an explicit
    # name. Its own guards decide whether the question points at a document at
    # all; see `app.document_referent`.
    #
    # It reads the candidate list Stage T just fetched rather than fetching
    # its own, so adding resolution costs zero extra catalog queries. When it
    # resolves, the referent is selected FIRST, ahead of the fill loop below,
    # so a document identified as the subject of the question can never be
    # crowded out of the body budget by a merely topical one.
    resolution = document_referent.Resolution()
    if not selected:
        known = [
            document_referent.KnownDocument(
                key=ref.id, title=ref.filename, provider=PROVIDER_UPLOADS
            )
            for ref in refs
        ] + [
            document_referent.KnownDocument(
                key=_connected_key(doc), title=doc.title, provider=doc.provider
            )
            for doc in connected
        ]
        # Candidates map back through the RENDERED lists only. A candidate
        # whose upload was deleted since it was catalogued, or which fell
        # below the index cap, has no Index entry — resolving to it would put
        # a body under "Contents loaded" for a document the Index does not
        # list, the one inconsistency the cap exists to prevent.
        by_external_id = {
            ref.id: document_referent.KnownDocument(
                key=ref.id, title=ref.filename, provider=PROVIDER_UPLOADS
            )
            for ref in refs
        }
        by_external_id.update({
            doc.external_id: document_referent.KnownDocument(
                key=_connected_key(doc), title=doc.title, provider=doc.provider
            )
            for doc in connected
        })
        try:
            resolution = document_referent.resolve(
                enterprise_id=enterprise_id,
                question=question,
                history=history,
                known=known,
                candidates=topical_candidates,
                by_external_id=by_external_id,
            )
        except Exception:  # noqa: BLE001 — resolution must never break an answer
            logger.warning(
                "document referent resolution failed for %s; answering with "
                "no referent", enterprise_id, exc_info=True,
            )
            resolution = document_referent.Resolution()

    if resolution.resolved:
        referent = resolution.referent
        chosen: _Chosen | None = None
        if referent.provider == PROVIDER_UPLOADS:
            ref = by_ref_id.get(referent.key)
            if ref is not None:
                chosen = _Chosen(
                    key=ref.id, name=ref.filename,
                    provider=PROVIDER_UPLOADS, ref=ref,
                )
        else:
            doc = next(
                (d for d in connected if _connected_key(d) == referent.key), None
            )
            if doc is not None:
                chosen = _Chosen(
                    key=referent.key, name=doc.title,
                    provider=doc.provider, catalog_doc=doc,
                )
        if chosen is None:
            # The referent named a document this turn will not render after
            # all. Drop the assertion rather than keep a heading pointing at
            # nothing — Stage T below still runs, unchanged.
            resolution = document_referent.Resolution()
        else:
            selected.append(chosen)
            match_by_id[chosen.key] = (resolution.how, None)

    for candidate in topical_candidates:
        if len(selected) >= MAX_SELECTED_DOCUMENTS:
            break
        # A chat attachment's text already reached the model through its
        # own turn's folded history, so ranking one is informative but
        # loading it again would duplicate it into the prompt. It is
        # accounted for below, not re-rendered, and does not consume a
        # body slot that a workspace document could use.
        if candidate["provider"] == PROVIDER_CHAT_ATTACHMENT:
            continue
        external_id = candidate["external_id"]
        if candidate["provider"] == PROVIDER_UPLOADS:
            ref = by_ref_id.get(external_id)
            # A catalog row whose upload has since been deleted (or is
            # below the index cap) has no body to resolve — skip rather
            # than render an entry with nothing behind it.
            if ref is None or ref.id in match_by_id:
                continue
            selected.append(_Chosen(
                key=ref.id, name=ref.filename,
                provider=PROVIDER_UPLOADS, ref=ref,
            ))
            match_by_id[ref.id] = ("topic", candidate["rank"])
            continue
        # A connected-source document. Same rule as above about the index
        # cap: only rows that are actually RENDERED may be selected, so a
        # body can never appear under "Contents loaded" for a document the
        # Index does not list.
        doc = connected_by_external_id.get(external_id)
        if doc is None:
            continue
        key = _connected_key(doc)
        if key in match_by_id:
            continue
        selected.append(_Chosen(
            key=key, name=doc.title, provider=doc.provider, catalog_doc=doc
        ))
        match_by_id[key] = ("topic", candidate["rank"])

    selected_ids = {chosen.key for chosen in selected}

    # ── Bodies. Resolved BEFORE the index renders, because whether a
    #    connected-source fetch succeeded is part of that document's index
    #    line: a page whose body could not be loaded says so there, next to
    #    its summary, instead of being silently marked loaded with nothing
    #    behind it.
    resolver = BodyResolver(enterprise_id)
    bodies: dict[str, str] = {}
    unavailable: dict[str, str] = {}
    if selected:
        per_doc_budget = max(1, _DOCUMENT_CHAR_BUDGET // len(selected))
        for chosen in selected:
            if chosen.ref is not None:
                text = get_file_text(enterprise_id, chosen.ref.id) or ""
            else:
                # At most MAX_SELECTED_DOCUMENTS of these per ask (3), so the
                # worst case is three page fetches — the cap that already
                # bounds how much document text an answer carries also bounds
                # how much network an answer does.
                resolved = resolver.resolve(chosen.provider, chosen.catalog_doc.external_id)
                if not resolved.resolved:
                    unavailable[chosen.key] = resolved.reason
                    continue
                text = resolved.text or ""
                if not text:
                    # Read successfully and genuinely empty. Distinct from
                    # unavailable, and said so: "this document has no text" is
                    # true, "we could not load it" would not be.
                    bodies[chosen.key] = "[This document has no readable text.]"
                    continue
            if len(text) > per_doc_budget:
                body = (
                    text[:per_doc_budget]
                    + f"\n[Truncated — showing the first {per_doc_budget} of "
                    f"{len(text)} characters of this document.]"
                )
            else:
                body = text
            bodies[chosen.key] = body

    lines = [
        f"# {DOCUMENT_INDEX_HEADER}",
        "",
        "## Index — every document this workspace has uploaded or connected",
    ]
    for ref in refs:
        date = (ref.uploaded_at or "")[:10]
        lines.append(_index_line(
            f"- {ref.filename} (source: {ref.source_name}, uploaded {date})",
            catalog.get(ref.id), ref.id in selected_ids, truncated_index,
        ))
    for turn_id, index, attachment, turn_created_at in conv_attachments:
        date = (turn_created_at or "")[:10]
        external_id = f"turn:{turn_id}:attachment:{index}"
        lines.append(_index_line(
            f"- {attachment['name']} (attached to this conversation, {date})",
            catalog.get(external_id), True, truncated_index,
        ))
    for doc in connected:
        key = _connected_key(doc)
        lines.append(_index_line(
            _connected_source_head(doc), doc, key in bodies, truncated_index,
            unavailable_reason=unavailable.get(key, ""),
        ))
    if truncated_index:
        # The marker states PARTIAL explicitly, because above the cap the
        # existence contract genuinely changes: the index is no longer the
        # complete inventory, so an absence from it stops being evidence of
        # absence. The prompt's existence rule keys off this wording.
        lines.append(
            f"[This list is PARTIAL: it shows {MAX_INDEX_ENTRIES} of {total} "
            f"documents in this workspace. A document missing from this list "
            f"may still exist.]"
        )

    # The referent (or the ambiguity) renders AFTER the Index and BEFORE the
    # bodies, so the model reads "here is everything that exists", then "this
    # is the one you are being asked about", then its text. Emitted from the
    # same constants `prompts.ASK_SYSTEM_DOCUMENTS_ADDENDUM` quotes, so the
    # rule and the marker cannot drift apart.
    lines.extend(document_referent.render_referent_block(resolution))

    if bodies:
        lines.append("")
        lines.append("## Contents loaded for this question")
        for chosen in selected:
            if chosen.key not in bodies:
                continue
            lines.append("")
            lines.append(f"### {chosen.name}")
            lines.append(bodies[chosen.key])

    block = "\n".join(lines)

    # `match` and `rank` are the audit trail that distinguishes "was never
    # selected" from "was selected and turned out unhelpful" — the two cases
    # that look identical in an answer but mean opposite things about whether
    # selection is working. Counts-only discipline still applies to the
    # decision log; this manifest is already per-document and server-derived.
    manifest = [
        {
            "file_id": ref.id,
            "filename": ref.filename,
            "source_name": ref.source_name,
            "uploaded_at": ref.uploaded_at,
            "loaded": ref.id in selected_ids,
            "scope": "workspace",
            "match": match_by_id.get(ref.id, (None, None))[0],
            "rank": match_by_id.get(ref.id, (None, None))[1],
        }
        for ref in refs
    ]
    manifest.extend(
        {
            "file_id": f"turn:{turn_id}:attachment:{index}",
            "filename": attachment["name"],
            "scope": "conversation",
            # Not chosen by either stage: this document reached the model
            # because the user attached it to this turn.
            "match": "attached",
            "rank": None,
            # Not a workspace upload — nothing named it "source: X" in the
            # index either (see B2/B3); left unset rather than invented.
            "source_name": None,
            "uploaded_at": turn_created_at,
            # The attachment's extracted text already reached the model via
            # this SAME turn's folded history (routes.ask._load_history,
            # part A of this fix) — true regardless of whether this prompt
            # also rendered it under "Contents loaded" above, which it does
            # not for conversation attachments (existence is this block's
            # job; Part A already delivers the content).
            "loaded": True,
        }
        for turn_id, index, attachment, turn_created_at in conv_attachments
    )
    manifest.extend(
        {
            "file_id": _connected_key(doc),
            "filename": doc.title,
            # The system it lives in, which is what a reader of this manifest
            # needs to tell a Confluence page from a same-named upload.
            "source_name": (
                doc.source_name or _PROVIDER_LABELS.get(doc.provider, doc.provider)
            ),
            "uploaded_at": doc.doc_date or doc.updated_at,
            # `loaded` means THE BODY REACHED THE PROMPT — never merely that
            # selection wanted it. A page selected and then unfetchable is
            # loaded=False, which is what keeps the manifest honest about a
            # degraded answer instead of recording an intent as a fact.
            "loaded": _connected_key(doc) in bodies,
            "scope": "workspace",
            "match": match_by_id.get(_connected_key(doc), (None, None))[0],
            "rank": match_by_id.get(_connected_key(doc), (None, None))[1],
        }
        for doc in connected
    )
    _log_document_selection(
        enterprise_id,
        manifest=manifest,
        catalog_size=len(catalog_docs),
        catalog_by_provider=_catalog_by_provider(catalog_docs),
        topical_ran=topical_ran,
        topical_candidates=len(topical_candidates),
        question_embedding=question_embedding,
        embedding_degraded=embedding_degraded,
        resolution=resolution,
        history_turns=len(history or []),
    )
    return block, manifest


#: How Stage T ended, as one value the audit spine can group by.
#: `catalog_empty` — nothing to search; a selection miss here means the
#:     document was never registered, which is an ingest problem.
#: `searched_no_match` — the catalog held documents, both channels ran, and
#:     neither ranked anything. A selection miss here is a ranking problem.
#: These two are indistinguishable in an answer and mean opposite things, so
#: they are recorded apart. `not_run` keeps them honest: Stage N had already
#: filled every slot, so no absence of topical matches can be read from it.
#: `no_documents_indexed` is the earlier exit still — the workspace has
#: nothing to show at all, so grounding returned before either stage.
TOPICAL_CATALOG_EMPTY = "catalog_empty"
TOPICAL_SEARCHED_NO_MATCH = "searched_no_match"
TOPICAL_RANKED = "ranked"
TOPICAL_NOT_RUN = "not_run"
TOPICAL_NO_INDEX = "no_documents_indexed"


def _topical_outcome(
    catalog_size: int,
    topical_ran: bool,
    topical_candidates: int,
    *,
    index_empty: bool = False,
) -> str:
    """Which Stage-T ending this ask hit. No new query: the caller already
    holds `catalog_docs` for the index it just rendered."""
    if index_empty:
        return TOPICAL_NO_INDEX
    if not topical_ran:
        return TOPICAL_NOT_RUN
    if catalog_size == 0:
        return TOPICAL_CATALOG_EMPTY
    return TOPICAL_RANKED if topical_candidates else TOPICAL_SEARCHED_NO_MATCH


def _catalog_by_provider(catalog_docs) -> dict[str, int]:
    """{provider: row count} for what this caller can see. Counts only."""
    counts: dict[str, int] = {}
    for doc in catalog_docs or []:
        provider = getattr(doc, "provider", None) or "unknown"
        counts[provider] = counts.get(provider, 0) + 1
    return counts


#: How Stage R ended. `none` is the majority outcome and the RIGHT one for a
#: question that is not about a document — it is recorded rather than being
#: the absence of a record, because "resolution declined" and "resolution
#: never ran" are opposite facts about the same silent answer.
REFERENT_NONE = "none"
REFERENT_AMBIGUOUS = "ambiguous"


def _referent_outcome(resolution) -> str:
    """`carried`, `resolved`, `ambiguous`, or `none`."""
    if resolution is None:
        return REFERENT_NONE
    if resolution.resolved:
        return resolution.how or REFERENT_NONE
    return REFERENT_AMBIGUOUS if resolution.ambiguous_titles else REFERENT_NONE


def _log_document_selection(
    enterprise_id: str,
    *,
    manifest: list[dict],
    catalog_size: int,
    topical_ran: bool,
    topical_candidates: int,
    question_embedding: list[float] | None,
    embedding_degraded: bool,
    index_empty: bool = False,
    resolution: "document_referent.Resolution | None" = None,
    history_turns: int = 0,
    catalog_by_provider: dict[str, int] | None = None,
) -> None:
    """Record how document selection went, from the ONE function both grounding
    call sites share.

    Written here rather than in `compose_ask_answer` because that is the direct
    path only. The skill-routed path — `qa_agent._answer_single_shot`, which is
    where most of this traffic goes — wrote none of this, so when topical
    selection returned the wrong document there for a whole day, nothing in the
    record said so. Putting the write in the shared callee is what gives both
    paths the same visibility without touching qa_agent.py.

    Costs one extra `agent_decision_log` row per ask, and on the direct path
    some of these counts also appear on that path's own `answer` row. That
    duplication is deliberate: the `answer` row is not written at all on the
    skill path, so removing the overlap here would take the parity back out.

    Counts and enums only — never a filename, a title, a summary or any
    document text. Best-effort: an audit write must never break an answer."""
    if not enterprise_id:
        return
    try:
        from app.graph.decision_log import log_agent_decision

        log_agent_decision(
            enterprise_id=enterprise_id,
            agent="ask",
            decision_type="document_selection",
            factors={
                "documents": len(manifest),
                "documents_loaded": sum(1 for d in manifest if d.get("loaded")),
                "documents_named": sum(
                    1 for d in manifest if d.get("match") == "named"
                ),
                "documents_topical": sum(
                    1 for d in manifest if d.get("match") == "topic"
                ),
                # How many rows the catalog held for this caller, which is what
                # separates "nothing to find" from "found nothing".
                "catalog_size": catalog_size,
                # The same separation, PER PROVIDER, and it is not a nicety.
                # `catalog_size` and `topical_outcome` are whole-catalog: a
                # tenant with 27 Confluence rows and ZERO Drive rows — which
                # is the real fleet-wide shape as of 2026-08-07, because
                # `drive_extract` only registers a file whose modifiedTime
                # moved — reports `ranked` and looks perfectly healthy while
                # Drive is structurally unreachable. Without this field, "this
                # tenant has no Drive documents catalogued" and "Drive had
                # documents and none matched" are the same record, and they
                # call for opposite fixes: run the backfill, versus look at
                # ranking. Counts only, keyed by provider — no titles.
                "catalog_by_provider": dict(sorted((catalog_by_provider or {}).items())),
                "topical_candidates": topical_candidates,
                "topical_outcome": _topical_outcome(
                    catalog_size, topical_ran, topical_candidates,
                    index_empty=index_empty,
                ),
                # Whether the semantic channel ran at all this ask. False here
                # with a non-empty catalog is the exact condition that made
                # ranking query-independent, and it was invisible.
                "semantic_channel": question_embedding is not None,
                "retrieval_embedding_degraded": embedding_degraded,
                # Stage R, as one enum. `none` covers by far the most asks and
                # is the CORRECT outcome for an ordinary business question —
                # a rising `resolved` rate against a flat `carried` one is the
                # shape a precision regression would take, and without this
                # field a false referent is indistinguishable in the record
                # from a topical load.
                "referent_outcome": _referent_outcome(resolution),
                # Whether Stage R had a conversation to resolve against at
                # all. Zero here on a follow-up question means the history
                # never reached grounding — the wiring defect, not a
                # resolution one, and the two look identical in an answer.
                "history_turns": history_turns,
            },
        )
    except Exception:  # noqa: BLE001 — audit write must not block the answer
        logger.exception(
            "document selection decision-log write failed for enterprise=%s",
            enterprise_id,
        )


# Prompt version stamped onto the Ask decision-log row so the §4d audit spine
# pins the exact Ask composition (corpus + KG bridge, #18) behind each answer.
ASK_PROMPT_VERSION = "ask-kg-v2"

# Strong refs to in-flight warm tasks. asyncio holds only a weak reference to a
# bare create_task result, so without this a fanned-out warm task can be
# garbage-collected mid-run (the warm silently dies). The done-callback discards
# each task on completion (mirrors routes/design_agent.py's _inflight_tasks).
_inflight_tasks: set[asyncio.Task] = set()


# Defined inline (and re-defined in routes/ask.py — keep in sync) so the
# warmer can run independent of the route module being imported first.
_ASK_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["source", "evidence"],
            },
        },
        "confidence": {"type": "number"},
        "unanswered": {"type": "string"},
    },
    "required": ["answer", "key_points", "citations", "confidence", "unanswered"],
}


def _generate_one_sync(dataset: str, question: str) -> dict:
    """Run the same Anthropic call that /v1/ask would run for a given Q.

    Only the warm paths call this (a user's ask goes through
    `compose_ask_answer`), so the call rides the LLM gate's low-priority
    background lane — pre-warming Ask answers must never queue a user's own
    generation behind it."""
    corpus = load_corpus(dataset)
    cacheable = f"Source material:\n\n{corpus.joined()}"
    user = ASK_USER_TEMPLATE_QUESTION_ONLY.format(question=question)
    # Warm/predefined asks carry only a dataset slug (no enterprise_id). The slug
    # IS the company slug (see deps.ownership), so resolve it to bind the
    # company's own Claude key when configured. Best-effort: an unresolvable slug
    # falls through to the platform key.
    from app.llm_keys import company_llm_key

    company_id = None
    try:
        from app.deps.ownership import company_id_for_dataset

        company_id = company_id_for_dataset(dataset)
    except Exception:  # noqa: BLE001 — key binding must never break warming
        company_id = None
    # This path calls `app.llm` directly rather than through the gateway, so the
    # usage label has to be stated here — the gateway's automatic
    # agent -> feature mapping doesn't apply.
    with company_llm_key(company_id), usage_scope(
        feature=Feature.ASK, operation="warm"
    ):
        return call_json(
            system=ASK_SYSTEM + today_line() + connected_sources_line(company_id),
            user=user,
            user_cacheable_prefix=cacheable,
            schema=_ASK_RESPONSE_SCHEMA,
            max_tokens=12000,
            background=True,
        )


def _question_embedding(
    enterprise_id: str | None, question: str
) -> tuple[list[float] | None, bool]:
    """Embed the question ONCE per ask. Returns `(vector, degraded)`.

    Computed here, at the top of the ask, because both consumers need it and
    neither can produce it for the other: `document_grounding` runs BEFORE
    knowledge-graph retrieval, so there is no earlier embedding to reuse, and
    on PRD-grounded asks KG retrieval never runs at all — on that branch this
    is the only embedding computed, and topical document selection would
    otherwise have no semantic channel whatsoever.

    `degraded` is True when no usable vector came back — no key configured
    (the accessor hands back a zero vector, which in cosine kNN ranks
    arbitrarily and is worse than nothing), or the call failed. Both consumers
    then rank on their lexical channels alone. The flag exists because that
    degradation is otherwise invisible: the system keeps answering, slightly
    worse, with nothing in the record to say why."""
    if not enterprise_id:
        return None, False
    try:
        from app.graph.embeddings import embed_texts

        vecs = embed_texts(
            [question], enterprise_id=enterprise_id, purpose="ask_retrieval"
        )
    except Exception as exc:  # noqa: BLE001 — never break an answer
        logger.info("Ask: question embedding unavailable (%s); lexical only", exc)
        return None, True
    vec = vecs[0] if vecs else None
    if not vec or not any(vec):
        logger.info(
            "Ask: question embedding unusable (missing or all-zero); "
            "document and KG retrieval rank on their lexical channels only"
        )
        return None, True
    return vec, False


def _resolve_question_embedding(
    enterprise_id: str | None, question: str
) -> tuple[list[float] | None, bool]:
    """This ask's `(vector, degraded)`, computing it only if nothing already
    has.

    `ask_job_runner._run_sync` SCOPES the slot before `answer()` picks a path
    but no longer fills it, so the first consumer that genuinely needs a vector
    computes it here and memoises it back — every later consumer then reads
    that one value and issues no call. An ask whose path never needs a vector
    (a doc-less tenant, or a PRD-grounded ask that skips KG retrieval) now
    issues none at all, where the old eager embed always paid for one.

    It still falls through to a plain `_question_embedding` for callers that
    reach `compose_ask_answer` without the worker in front of them — direct
    invocation, and the tests that do the same. Those have no scoped slot, so
    nothing is memoised and their behaviour is byte-for-byte what it was.

    Read the ContextVar directly rather than through
    `_carried_question_embedding`: that helper collapses `_EMBED_PENDING` to
    None, and the whole decision here turns on telling those two apart."""
    slot = _active_question_embedding.get()
    if slot is not None and slot is not _EMBED_PENDING:
        return slot  # type: ignore[return-value]
    resolved = _question_embedding(enterprise_id, question)
    if slot is _EMBED_PENDING:
        # Memoise onto the slot the worker scoped for us. Writing only in this
        # branch is what keeps the unscoped path leak-free: with no token to
        # unwind it, a write there would outlive the request on this pooled
        # thread — the exact hazard `ask_job_runner`'s `finally` exists to close.
        _active_question_embedding.set(resolved)
    return resolved


def _retrieve_kg_bundle(
    enterprise_id: str | None,
    question: str,
    *,
    question_embedding: list[float] | None = None,
    embedding_unavailable: bool = False,
) -> dict | None:
    """Best-effort KG retrieval for the Ask question (#18). Returns the bundle
    or None when there's no tenant context or the KG yields nothing / errors.

    `embedding_unavailable` carries forward `_question_embedding`'s degraded
    flag: True means the caller already determined there is no usable vector
    (no key configured, or the accessor's own zero-vector check tripped) —
    passed through to `retrieve_context` as `skip_semantic` so the KG neither
    runs kNN on a zero vector nor re-embeds a question that's already known to
    embed to nothing.

    Resilient by construction: a missing tenant, an empty KG, a fake backend
    with no pgvector, or any read failure all collapse to None so the caller
    runs the legacy corpus-only path (pre-#18 behaviour)."""
    if not enterprise_id:
        return None
    try:
        from app.graph.facade import GraphFacade
        from app.graph.retrieval import retrieve_context

        facade = GraphFacade()
        bundle = retrieve_context(
            facade, enterprise_id, question,
            question_embedding=question_embedding,
            skip_semantic=embedding_unavailable,
        )
    except Exception:  # noqa: BLE001 — KG must never break Ask
        logger.exception("Ask KG retrieval failed for enterprise=%s", enterprise_id)
        return None
    if not bundle or bundle.get("empty"):
        return None
    return bundle


# How long the whole gather may take before we give up waiting on it. A
# BACKSTOP, not a tuning knob: every leg already owns a timeout of its own
# (`live_read.BUDGET_S` is 8s across all connectors, the embedding has an HTTP
# timeout, the pgvector reads have theirs), so in normal operation this never
# fires. It exists for the one case those cannot cover — a leg that hangs on
# something with no timeout at all — and it is set well above their sum so it
# cannot silently start policing them.
#
# Deliberately NOT tight. A short deadline here would convert a visible latency
# problem into an invisible quality one: dropping the KG bundle or the document
# block does not degrade an answer gracefully, it produces a confident answer
# built on less, and nothing downstream can tell. The connector leg is the only
# one whose loss is already contracted for (an unread source is reported as
# unread), and it polices itself. So when this DOES fire it is a bug report —
# hence the warning naming exactly which legs were still outstanding.
_GATHER_DEADLINE_S = 25.0


def _gather(tasks: dict, deadline_s: float = _GATHER_DEADLINE_S) -> dict:
    """Run independent retrievals concurrently; return {name: result}.

    Each task runs on a COPY of the calling thread's context
    (`contextvars.copy_context`), which is load-bearing rather than tidy:
    `document_grounding` resolves conversation-scoped documents through
    `_active_conversation_id` / `_active_conversation_user_id`, and a bare
    `ThreadPoolExecutor.submit` does NOT carry those across (only
    `asyncio.to_thread` copies context for free — see this module's note on
    request-scoped ContextVars). Without the copy, a conversation's own
    documents would vanish from grounding with no error and no log: a quietly
    worse answer, which is the worst failure this change could have.

    NOTHING here may memoise back into a ContextVar. A write inside a copied
    context dies with it, so `_resolve_question_embedding` — which memoises the
    vector it computes — is deliberately resolved by the CALLER on this thread
    and passed in explicitly, not run as a task. Doing it here would have every
    consumer recompute, reintroducing the double-embed the pending-slot
    mechanism exists to prevent.

    A task that raises yields None for its slot rather than taking the others
    down: each retrieval already has its own fail-open contract (live_read
    "never raises", KG is best-effort) and this preserves them under fan-out.
    """
    if not tasks:
        return {}
    results: dict = {name: None for name in tasks}
    # NOT a `with` block. ThreadPoolExecutor.__exit__ calls shutdown(wait=True),
    # which blocks until every thread finishes — so a leg that blew the deadline
    # would still hold the ask for as long as it hung, and the deadline would
    # bound nothing at all. Shut down without waiting instead (below).
    pool = _futures.ThreadPoolExecutor(
        max_workers=len(tasks), thread_name_prefix="ask-gather"
    )
    try:
        # ONE COPY PER TASK. A `Context` can only be entered once at a time, so
        # sharing a single copy across concurrent tasks raises "cannot enter
        # context: ... is already entered" on whichever starts second — and this
        # function catches it, so every leg after the first degraded to None and
        # the answer was composed with no grounding while still reporting
        # success. Copies are taken HERE, on the calling thread, so each carries
        # the request's ContextVars.
        submitted = {
            pool.submit(contextvars.copy_context().run, fn): name
            for name, fn in tasks.items()
        }
        done, not_done = _futures.wait(submitted, timeout=deadline_s)
        for fut in not_done:
            logger.warning(
                "ask gather: %r did not finish within %.1fs — composing without it",
                submitted[fut], deadline_s,
            )
            fut.cancel()
        for fut in done:
            name = submitted[fut]
            try:
                results[name] = fut.result()
            except Exception:  # noqa: BLE001 — one leg failing never loses the ask
                logger.warning(
                    "ask gather: %r failed; composing without it", name, exc_info=True
                )
    finally:
        # A thread that overran is abandoned rather than waited on: its result is
        # already discarded, and the user's answer must not queue behind it. It
        # finishes on its own and the pool is collected once it does.
        pool.shutdown(wait=False, cancel_futures=True)
    return results


def compose_ask_answer(
    dataset: str,
    question: str,
    *,
    enterprise_id: str | None = None,
    prd_context: str = "",
    history: list[dict] | None = None,
    live_context: str = "",
    live_context_fn=None,
    on_delta=None,
) -> dict:
    """Generate an Ask answer from BOTH the legacy corpus AND the knowledge
    graph (#18 — chat answers from the brain, not only the markdown corpus).

    Flow:
      - PRD-grounded asks (`prd_context` set — PRD-tab chat) skip BOTH the
        corpus load and the KG retrieval: the PRD context block is the
        grounding and rides the cacheable user prefix (see inline comment).
      - Otherwise, load the dataset corpus (cacheable prefix; unchanged grounding).
      - If a tenant (`enterprise_id`) is resolvable AND its KG has relevant
        signals/entities, retrieve a ranked, budget-capped context bundle and
        inject it as a "LIVE CONTEXT FROM CONNECTED SOURCES" section, with the KG-aware
        system addendum. Otherwise fall back to corpus-only — identical to the
        pre-#18 path, including the cache warmer's prompt.
      - Decision-log the ask (agent="ask", decision_type="answer") with
        kg_refs = the signal/entity ids that fed the answer.

    `question` is the user's bare current-turn message — never fold prior
    turns into it. It drives all FOUR retrieval consumers below (the shared
    embedding, KG theme kNN, the document catalog's lexical channel, and
    Stage N filename matching), so a folded thread turns each of those into a
    thread-wide search instead of a question-scoped one — see `history` below
    for where prior turns actually belong.

    `history`, when given, is rendered once (`render_history_block`, same
    budget/clamping as every other fold site) and prepended to the composed
    user turn ahead of the question — the model still sees the whole
    conversation; retrieval never does. Mirrors what
    `qa_agent._answer_single_shot` already does for the skill-routed path.

    `live_context`, when given, is a pre-assembled block of LIVE reads from the
    company's connected tools (app/connector_lookup/sweep.py — the caller runs
    the sweep, this function only composes it). It rides the SAME slot as the KG
    bundle, and for the same reason: both are per-question retrieval and neither
    may enter the cacheable prefix, which exists to be byte-stable across every
    ask in a dataset. A sweep block in that prefix would invalidate the shared
    corpus cache on every single question.

    It is a FIFTH consumer of the bare `question` above, and for exactly the
    reason stated there: the sweep derives keyword terms, so a folded thread
    would search every connector for the previous turn's vocabulary. The sweep
    has always run on the raw question — that independent choice and this
    contract now agree, rather than one having to be retrofitted to the other.

    It is composed even when the KG bundle is empty — a company whose graph has
    nothing on a topic but whose Jira and Slack do is exactly the case this
    exists for — and it never reaches the PRD-grounded branch, which skips
    corpus and KG retrieval by design and would lose that saving.

    `on_delta`, when given, receives the PARTIAL-JSON fragments of the streamed
    tool input as the model writes them (the call switches to the streaming
    transport + long read timeout, mirroring the gateway's long-output path).
    The Ask worker wraps it in app.ask_stream.AnswerFieldExtractor to
    token-stream just the answer text to the client — progressive display only;
    the returned payload stays the authoritative answer.

    Returns the raw response payload (answer/key_points/citations/...); the
    caller strips citations + logs to ask_log as before."""
    try:
        history_block = render_history_block(history)
    except Exception:  # noqa: BLE001 — history is prompt context, never the
        # reason retrieval or the answer fails; degrade to no history block
        # rather than lose the ask.
        logger.warning(
            "history render failed for enterprise=%s; answering without "
            "conversation context",
            enterprise_id, exc_info=True,
        )
        history_block = ""
    # ── Gather, in two waves ────────────────────────────────────────────────
    #
    # These retrievals used to run one after another — connectors, then the
    # embedding, then document grounding, then the corpus, then the KG — and
    # measured ~21s between the planner's verdict and the first answer token.
    # None of them feeds another. The only real ordering is that BOTH document
    # grounding and KG retrieval consume the question vector, so the shape is
    # two waves with the embedding as the barrier between them, and the cost
    # becomes the slowest leg instead of the sum.
    #
    # THE BRANCH IS DECIDED FIRST, deliberately. The PRD-grounded path skips the
    # corpus load and KG retrieval for cost reasons; starting either eagerly
    # "because it might be needed" would spend exactly what that branch exists
    # to save. Document grounding runs on BOTH branches — a PRD-tab chat must
    # not go blind to uploads.
    wants_corpus_and_kg = not prd_context

    # WAVE 1 — everything that needs nothing.
    #
    # The embedding is resolved on THIS thread rather than as a task: it
    # memoises the vector it computes back into a ContextVar, and a write inside
    # a copied context would be lost (see `_gather`). Running it here keeps that
    # memoisation real AND still overlaps it with the pool's work.
    wave1: dict = {"facts": lambda: company_facts_block(enterprise_id)}
    if live_context_fn is not None:
        wave1["live"] = live_context_fn
    if wants_corpus_and_kg:
        wave1["corpus"] = lambda: load_corpus(dataset)

    # Not a `with` block, for the reason `_gather` gives: shutdown(wait=True)
    # would make the deadline bound nothing.
    pool = _futures.ThreadPoolExecutor(
        max_workers=max(1, len(wave1)), thread_name_prefix="ask-gather-1"
    )
    try:
        # One copy per task — see `_gather` for why sharing one is a bug.
        w1 = {
            pool.submit(contextvars.copy_context().run, fn): name
            for name, fn in wave1.items()
        }
        # The barrier that wave 2 genuinely needs, computed while wave 1 runs.
        question_embedding, embedding_degraded = _resolve_question_embedding(
            enterprise_id, question
        )
        done1, pending1 = _futures.wait(w1, timeout=_GATHER_DEADLINE_S)
        gathered: dict = {name: None for name in wave1}
        for fut in pending1:
            logger.warning(
                "ask gather: %r did not finish within %.1fs — composing without it",
                w1[fut], _GATHER_DEADLINE_S,
            )
            fut.cancel()
        for fut in done1:
            try:
                gathered[w1[fut]] = fut.result()
            except Exception:  # noqa: BLE001 — one leg never loses the ask
                logger.warning(
                    "ask gather: %r failed; composing without it", w1[fut], exc_info=True
                )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    facts = gathered.get("facts") or ""
    # A caller that pre-computed the block still wins; `live_context_fn` is the
    # concurrent route and only qa_agent's planned path uses it.
    live_context = live_context or (gathered.get("live") or "")
    corpus = gathered.get("corpus") if wants_corpus_and_kg else None

    # WAVE 2 — the two consumers of the vector, which do not feed each other.
    #
    # `history` threads in explicitly here because this call site HAS it as a
    # parameter — the ContextVar route exists for `qa_agent._answer_single_shot`,
    # which does not. Passed to RESOLUTION only: `question` stays bare, so the
    # name and topic stages still see what the user typed, not the thread.
    wave2: dict = {
        "docs": lambda: document_grounding(
            enterprise_id, question,
            question_embedding=question_embedding, history=history,
        ),
    }
    if wants_corpus_and_kg:
        wave2["kg"] = lambda: _retrieve_kg_bundle(
            enterprise_id, question, question_embedding=question_embedding,
            embedding_unavailable=embedding_degraded,
        )
    w2 = _gather(wave2)
    docs_block, documents = w2.get("docs") or ("", [])
    kg_bundle = w2.get("kg")

    if prd_context:
        # PRD-grounded ask (PRD-tab chat): the PRD context block (PRD + insight
        # + evidence + tickets + prototype, ~26K tokens) dominates the prompt
        # and IS the grounding — skip both the corpus load and the KG retrieval
        # (an OpenAI embeddings HTTP call + pgvector queries, ~0.5-1s serial)
        # entirely.
        #
        # The block rides the CACHEABLE user prefix, not plain `input`: it is
        # byte-stable across turns of the same PRD conversation (same PRD
        # content → same string), so turn 1 pays one cache write and turns 2+
        # cache-read the whole block instead of re-prefilling it. History and
        # the question stay in the uncached `user` suffix. The old concern
        # about keeping per-PRD text out of the cacheable prefix was about
        # fragmenting the SHARED corpus prefix for plain asks — it doesn't
        # apply here, because this prefix replaces the corpus one and exists
        # only for PRD-grounded asks.
        from app.prompts import ASK_SYSTEM_PRD_ADDENDUM

        bundle = None
        system = (ASK_SYSTEM + ASK_SYSTEM_PRD_ADDENDUM + today_line()
                  + connected_sources_line(enterprise_id))
        user = history_block + ASK_USER_TEMPLATE_QUESTION_ONLY.format(question=question)
        cacheable = prd_context
    else:
        # Both were gathered above (wave 1 / wave 2) — read, don't re-fetch.
        cacheable = (
            f"Source material:\n\n{corpus.joined()}"
            if corpus is not None and corpus.docs
            else None
        )
        bundle = kg_bundle

        # The KG bundle and the live sweep share one "connected sources" slot.
        # Keeping them in ONE section (rather than adding a second template)
        # is what stops the prompt fragmenting into four shapes, and it is
        # also true to what they are: two readers of the same connected
        # sources, one a sync-time snapshot and one read just now. The
        # addendum below is what tells the model which is which.
        context_sections: list[str] = []
        if bundle:
            from app.graph.retrieval import render_context_section

            context_sections.append(render_context_section(bundle))
        if live_context:
            context_sections.append(live_context)

        if context_sections:
            # Each addendum is gated on ITS OWN section being present. The KG
            # addendum names a "LIVE CONTEXT FROM CONNECTED SOURCES" heading
            # that only `render_context_section` emits, so appending it for a
            # sweep-only prompt would point the model at a section that is not
            # there.
            system = (ASK_SYSTEM
                      + (ASK_SYSTEM_KG_ADDENDUM if bundle else "")
                      + (ASK_SYSTEM_LIVE_SWEEP_ADDENDUM if live_context else "")
                      + today_line() + connected_sources_line(enterprise_id))
            user = history_block + ASK_USER_TEMPLATE_WITH_KG.format(
                kg_context="\n\n---\n\n".join(context_sections), question=question
            )
        else:
            system = (ASK_SYSTEM + today_line()
                      + connected_sources_line(enterprise_id))
            user = history_block + ASK_USER_TEMPLATE_QUESTION_ONLY.format(question=question)

    # Self-reported workspace identity (interim incident fix): computed once
    # above so it rides EVERY branch's cacheable prefix, first — a long corpus
    # or PRD block can never push it out. facts == "" ⇒ cacheable/system are
    # byte-identical to the pre-fix composition (including the None case).
    #
    # `docs_block` trails the corpus/PRD/KG block, not the other way around.
    # Prompt caching is prefix-matched: it hits only up to the first differing
    # byte. `docs_block` is stamped with a per-question "[loaded for this
    # question]"/"[not loaded]" marker on every catalogued document
    # (`_index_line`) and carries the selected bodies themselves, so it is
    # volatile on every single ask. `facts` and the corpus/PRD block are
    # stable (per tenant, per dataset/PRD respectively). Putting the volatile
    # block last means the shared prefix — the whole stable part — still
    # matches across two different questions in the same dataset; putting it
    # first (the old order) invalidated the cache on every ask.
    cacheable = (
        "\n\n---\n\n".join(p for p in (facts, cacheable, docs_block) if p) or None
    )
    if facts:
        system += ASK_SYSTEM_COMPANY_FACTS_ADDENDUM
    if docs_block:
        system += ASK_SYSTEM_DOCUMENTS_ADDENDUM

    # Bind the tenant's own Claude key (when configured) for this direct
    # (non-gateway) answer call. See app.llm_keys.
    from app.llm_keys import company_llm_key

    # Best-effort cache/usage telemetry for the answer decision-log row below
    # — makes the cache-prefix reorder's win measurable rather than asserted.
    # `call_json` populates this from the provider's usage object; it stays
    # `{}` (and every counter below defaults to 0) if the provider returns
    # none.
    meta_out: dict = {}
    with company_llm_key(enterprise_id):
        payload = call_json(
            system=system,
            user=user,
            user_cacheable_prefix=cacheable,
            schema=_ASK_RESPONSE_SCHEMA,
            max_tokens=12000,
            meta_out=meta_out,
            # Token-streaming a chat answer implies the streaming transport
            # (and its long read timeout) — same pattern as the gateway.
            stream=on_delta is not None,
            timeout=LONG_REQUEST_TIMEOUT_S if on_delta is not None else None,
            on_json_delta=on_delta,
        )

    # Server-derived, never model-authored — the model attributes a loaded
    # document by filename inline; this is what lets the client resolve that
    # attribution to a durable file_id (or show the held-but-not-loaded set).
    payload["documents"] = documents

    # Decision-log the ask onto the §4d audit spine. Best-effort + tenant-
    # scoped — only when a tenant resolved (legacy cookie sessions have none).
    if enterprise_id:
        try:
            from app.graph.decision_log import log_agent_decision

            log_agent_decision(
                enterprise_id=enterprise_id,
                agent="ask",
                decision_type="answer",
                factors={
                    "dataset": dataset,
                    "question": question,
                    "kg_used": bool(bundle),
                    # Whether the answer additionally read connected tools LIVE.
                    # Recorded because a sweep that silently stops firing —
                    # a flag flipped, a connection expiring, terms never
                    # extracting — looks exactly like "the sweep found
                    # nothing" from the outside, and nothing else in the
                    # record would distinguish them.
                    "live_sweep": bool(live_context),
                    "live_sweep_chars": len(live_context),
                    "prd_grounded": bool(prd_context),
                    "kg_signals": len(bundle["signals"]) if bundle else 0,
                    "kg_themes": len(bundle["themes"]) if bundle else 0,
                    # Counts only — never filenames or document text.
                    "documents": len(documents),
                    "documents_loaded": sum(d["loaded"] for d in documents),
                    # Which STAGE did the loading, as counts. Selection
                    # quality is otherwise unobservable after the fact: a
                    # sudden collapse in topic-matched loads is the signal
                    # that ranking has drifted, and it looks identical to
                    # "nobody asked about documents" without this split.
                    "documents_named": sum(
                        1 for d in documents if d.get("match") == "named"
                    ),
                    "documents_topical": sum(
                        1 for d in documents if d.get("match") == "topic"
                    ),
                    # The semantic channel was unavailable for this ask, so
                    # topical selection ran on keyword matching alone. Recorded
                    # because the system keeps answering — slightly worse —
                    # and nothing else in the record would say why.
                    "retrieval_embedding_degraded": embedding_degraded,
                    # Cache/usage counts only — never text (see the rule
                    # above this block). Makes the cacheable-prefix ordering
                    # measurable: a healthy cache hit shows up here as a
                    # non-zero `cache_read_input_tokens` on the second+ ask
                    # in a dataset within the cache TTL.
                    "cache_read_input_tokens": meta_out.get(
                        "cache_read_input_tokens", 0
                    ),
                    "cache_creation_input_tokens": meta_out.get(
                        "cache_creation_input_tokens", 0
                    ),
                    "input_tokens": meta_out.get("input_tokens", 0),
                },
                output={
                    "key_points": payload.get("key_points", []),
                    "unanswered": payload.get("unanswered", ""),
                },
                model=DEFAULT_MODEL,
                prompt_version=ASK_PROMPT_VERSION,
                confidence=payload.get("confidence"),
                kg_refs=(bundle or {}).get("kg_refs") or [],
            )
        except Exception:  # noqa: BLE001 — audit write must not block the answer
            logger.exception("Ask decision-log write failed for enterprise=%s", enterprise_id)

    return payload


async def _warm_one(dataset: str, question: str, sema: asyncio.Semaphore) -> None:
    """Generate + cache the response for a single predefined prompt.

    No-op if a ready/generating row already exists for (dataset, question).
    Errors are logged + stored on the cache row; do not propagate.
    """
    if find_cached_ask(dataset, question):
        logger.info("Cached Ask already exists for %s · %s", dataset, question[:60])
        return
    cache_id = start_cached_ask(
        dataset=dataset,
        question=question,
        cache_version=ASK_CACHE_VERSION,
    )
    logger.info(
        "Warming cached Ask id=%s dataset=%s q=%r",
        cache_id,
        dataset,
        question[:80],
    )
    try:
        async with sema:
            payload = await asyncio.to_thread(_generate_one_sync, dataset, question)
        complete_cached_ask(cache_id, json.dumps(payload))
        logger.info("Cached Ask ready id=%s", cache_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Cached Ask warming failed id=%s", cache_id)
        fail_cached_ask(cache_id, msg)


def warm_predefined_asks(dataset: str, sema: asyncio.Semaphore) -> None:
    """Fan out warm tasks for every predefined prompt. Returns immediately;
    each warm task runs concurrently under the shared semaphore so we don't
    burst-fire Anthropic on top of brief / evidence / PRD warming.
    """
    for prompt in PREDEFINED_ASK_PROMPTS:
        task = asyncio.create_task(_warm_one(dataset, prompt, sema))
        _inflight_tasks.add(task)
        task.add_done_callback(_inflight_tasks.discard)


def warm_brief_dynamic_asks(
    dataset: str, brief: dict, sema: asyncio.Semaphore
) -> None:
    """Warm the per-insight Ask prompts that the BriefScreen fires when the
    user clicks "Ask Sprntly" on a finding card.

    Frontend pattern (web/app/lib/brief-adapter.ts):
        askQuestion: `Tell me more about: ${insight.title}`

    For each insight in the brief, we precompute the same text and warm a
    cache row so the click renders instantly.
    """
    for insight in brief.get("insights") or []:
        title = (insight or {}).get("title")
        if not title:
            continue
        prompt = f"Tell me more about: {title}"
        task = asyncio.create_task(_warm_one(dataset, prompt, sema))
        _inflight_tasks.add(task)
        task.add_done_callback(_inflight_tasks.discard)
