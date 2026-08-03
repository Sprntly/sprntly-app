"""Background warmer for the predefined Ask prompts.

The home / Ask-Sprntly screens have a small fixed set of starter chips
(see `PREDEFINED_ASK_PROMPTS` in prompts.py). Each click sends the same
question text every time. By pre-generating responses at brief-creation
time we make those clicks render instantly — the route just returns a
cached row.
"""
import asyncio
import contextvars
import json
import logging
import re
from urllib.parse import urlparse

from app.corpus import load_corpus
from app.db import (
    complete_cached_ask,
    fail_cached_ask,
    find_cached_ask,
    start_cached_ask,
)
from app.document_catalog import (
    PROVIDER_CHAT_ATTACHMENT,
    PROVIDER_UPLOADS,
    find_candidates as find_catalog_candidates,
    list_documents as list_catalog_documents,
)
from app.document_sources import DocumentFileRef, get_file_text, list_company_files
from app.llm import DEFAULT_MODEL, LONG_REQUEST_TIMEOUT_S, call_json
from app.usage_context import Feature, usage_scope
from app.prompts import (
    ASK_CACHE_VERSION,
    ASK_SYSTEM,
    ASK_SYSTEM_COMPANY_FACTS_ADDENDUM,
    ASK_SYSTEM_DOCUMENTS_ADDENDUM,
    ASK_SYSTEM_KG_ADDENDUM,
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

#: Providers whose bodies THIS module can actually resolve, and therefore the
#: only ones selection may choose. Uploads resolve through
#: `document_sources.get_file_text`; chat attachments reach the model through
#: their own turn's folded history.
#:
#: Drive files and Confluence pages are catalogued and summarised, so the rank
#: will surface them — but nothing here can fetch their bodies. Selecting one
#: would render an index entry marked loaded with no contents behind it and
#: push the model straight back to "I have this document but could not load
#: its contents", which is the exact answer this change exists to stop
#: producing. Excluding them leaves both providers behaving precisely as they
#: do today: indexed, never falsely promised. Delete this set and its two uses
#: when body resolution for those providers lands — it is a temporary
#: predicate with a named owner, not a permanent policy.
SELECTABLE_PROVIDERS = frozenset({PROVIDER_UPLOADS, PROVIDER_CHAT_ATTACHMENT})

#: Candidates requested from the hybrid rank per question. Comfortably above
#: MAX_SELECTED_DOCUMENTS so `SELECTABLE_PROVIDERS` has headroom to discard
#: unresolvable rows without starving selection. A capacity number, not a
#: relevance one: raising it cannot change which document ranks first, only
#: how deep the provider filter can dig before it runs out of rows.
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
        if row.get("provider") not in SELECTABLE_PROVIDERS:
            continue
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


def _catalog_by_external_id(
    enterprise_id: str, conversation_id: int | None, user_id: str | None
) -> dict[str, object]:
    """`{external_id: CatalogDocument}` for the rows this caller may see,
    limited to providers selection can resolve.

    Used only to ENRICH index lines the uploads/attachment reads already
    produced — the catalog is not the index's spine, so a catalog that is
    empty, stale or unreadable costs summaries and topics, never existence.
    That is what keeps a catalog outage a degradation instead of an
    incident."""
    try:
        docs = list_catalog_documents(
            enterprise_id, conversation_id=conversation_id, user_id=user_id
        )
    except Exception:  # noqa: BLE001 — enrichment must never break an answer
        logger.warning(
            "document catalog read failed for %s; index renders without "
            "summaries", enterprise_id, exc_info=True,
        )
        return {}
    return {
        doc.external_id: doc
        for doc in docs
        if doc.provider in SELECTABLE_PROVIDERS
    }


def _index_line(
    head: str, catalog_doc, loaded: bool, partial_index: bool
) -> str:
    """One index entry: the existing `- {name} ({scope}, {date})` head, then
    the catalog's one-line summary, then its topics, then whether the body is
    loaded for THIS question.

    The summary is the routing hint that lets the model notice a selection
    mistake — "the loaded documents don't cover this, but X looks relevant" —
    which is the point of showing it. The loaded-marker is what stops the
    model treating a one-line summary as if it had read the document."""
    parts = [head]
    if catalog_doc is not None:
        summary = (catalog_doc.summary or "").strip()
        if summary:
            parts.append(f"— {summary}")
        topics = [t for t in (catalog_doc.topics or []) if t]
        if topics:
            parts.append(f"Topics: {', '.join(topics)}.")
    parts.append(
        "[loaded for this question]" if loaded else "[not loaded for this question]"
    )
    return " ".join(parts)


def document_grounding(
    enterprise_id: str | None,
    question: str,
    conversation_id: int | None = None,
    *,
    question_embedding: list[float] | None = None,
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

    Selection runs in two stages. **Stage N** loads the documents the question
    NAMES (`_select_documents`, substring match, binary, no tunable). **Stage
    T** fills whatever slots remain with the documents the question is ABOUT,
    by the catalog's fused lexical+semantic rank (`_topical_candidates`, no
    floor, no threshold). Named beats topical always.

    `question_embedding` is computed ONCE per ask by the caller and threaded
    in, because this function runs BEFORE knowledge-graph retrieval and on
    PRD-grounded asks that retrieval never runs at all — so there is no
    existing embedding here to reuse. Passing None is legitimate and means
    Stage T ranks lexically only (see `_topical_candidates`); callers that
    care about the degradation record it, since this function's two-value
    return is fixed by its other call site.

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

    if conversation_id is None:
        conversation_id = _active_conversation_id.get()
    caller_user_id = _active_conversation_user_id.get()
    conv_attachments = _owned_conversation_attachments(
        enterprise_id, conversation_id, caller_user_id
    )

    if not refs and not conv_attachments:
        return "", []

    total = len(refs)
    truncated_index = total > MAX_INDEX_ENTRIES
    # Truncate BEFORE selecting: if selection ran against the untruncated
    # list, a matched document ranked outside the visible index could have
    # its body rendered under "Contents loaded" while being absent from both
    # the rendered Index and the manifest — reproducing, inside a
    # >MAX_INDEX_ENTRIES tenant, the exact content-present/existence-missing
    # inconsistency this ticket exists to eliminate.
    refs = refs[:MAX_INDEX_ENTRIES]

    catalog = _catalog_by_external_id(enterprise_id, conversation_id, caller_user_id)

    # ── Stage N: the documents the question NAMES. These load first, and a
    #    ranking never displaces them.
    selected = _select_documents(question, refs)
    match_by_id: dict[str, tuple[str, int | None]] = {
        ref.id: ("named", None) for ref in selected
    }

    # ── Stage T: fill whatever slots remain by fused rank.
    by_ref_id = {ref.id: ref for ref in refs}
    if len(selected) < MAX_SELECTED_DOCUMENTS:
        for candidate in _topical_candidates(
            enterprise_id,
            question,
            question_embedding=question_embedding,
            conversation_id=conversation_id,
            user_id=caller_user_id,
            exclude_external_ids=set(match_by_id),
        ):
            if len(selected) >= MAX_SELECTED_DOCUMENTS:
                break
            # A chat attachment's text already reached the model through its
            # own turn's folded history, so ranking one is informative but
            # loading it again would duplicate it into the prompt. It is
            # accounted for below, not re-rendered, and does not consume a
            # body slot that a workspace document could use.
            if candidate["provider"] == PROVIDER_CHAT_ATTACHMENT:
                continue
            ref = by_ref_id.get(candidate["external_id"])
            # A catalog row whose upload has since been deleted (or is below
            # the index cap) has no body to resolve — skip rather than render
            # an entry with nothing behind it.
            if ref is None or ref.id in match_by_id:
                continue
            selected.append(ref)
            match_by_id[ref.id] = ("topic", candidate["rank"])

    selected_ids = {ref.id for ref in selected}

    lines = [
        f"# {DOCUMENT_INDEX_HEADER}",
        "",
        "## Index — every document this workspace has uploaded",
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
    if truncated_index:
        # The marker states PARTIAL explicitly, because above the cap the
        # existence contract genuinely changes: the index is no longer the
        # complete inventory, so an absence from it stops being evidence of
        # absence. The prompt's existence rule keys off this wording.
        lines.append(
            f"[This list is PARTIAL: it shows the {MAX_INDEX_ENTRIES} most "
            f"recently uploaded of {total} documents in this workspace. A "
            f"document missing from this list may still exist.]"
        )

    bodies: dict[str, str] = {}
    if selected:
        per_doc_budget = max(1, _DOCUMENT_CHAR_BUDGET // len(selected))
        for ref in selected:
            text = get_file_text(enterprise_id, ref.id) or ""
            if len(text) > per_doc_budget:
                body = (
                    text[:per_doc_budget]
                    + f"\n[Truncated — showing the first {per_doc_budget} of "
                    f"{len(text)} characters of this document.]"
                )
            else:
                body = text
            bodies[ref.id] = body

    if bodies:
        lines.append("")
        lines.append("## Contents loaded for this question")
        for ref in selected:
            lines.append("")
            lines.append(f"### {ref.filename}")
            lines.append(bodies[ref.id])

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
    return block, manifest


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


def _retrieve_kg_bundle(
    enterprise_id: str | None,
    question: str,
    *,
    question_embedding: list[float] | None = None,
) -> dict | None:
    """Best-effort KG retrieval for the Ask question (#18). Returns the bundle
    or None when there's no tenant context or the KG yields nothing / errors.

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
        )
    except Exception:  # noqa: BLE001 — KG must never break Ask
        logger.exception("Ask KG retrieval failed for enterprise=%s", enterprise_id)
        return None
    if not bundle or bundle.get("empty"):
        return None
    return bundle


def compose_ask_answer(
    dataset: str,
    question: str,
    *,
    enterprise_id: str | None = None,
    prd_context: str = "",
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

    `on_delta`, when given, receives the PARTIAL-JSON fragments of the streamed
    tool input as the model writes them (the call switches to the streaming
    transport + long read timeout, mirroring the gateway's long-output path).
    The Ask worker wraps it in app.ask_stream.AnswerFieldExtractor to
    token-stream just the answer text to the client — progressive display only;
    the returned payload stays the authoritative answer.

    Returns the raw response payload (answer/key_points/citations/...); the
    caller strips citations + logs to ask_log as before."""
    facts = company_facts_block(enterprise_id)
    # ONE embedding per ask, computed before either consumer and shared by
    # both. Document grounding runs first and the PRD branch skips KG
    # retrieval entirely, so there is no existing vector either could inherit
    # — computing it here is what stops this being two calls (or, on the PRD
    # branch, no semantic channel at all).
    question_embedding, embedding_degraded = _question_embedding(
        enterprise_id, question
    )
    # Computed once, beside `facts`, so it rides EVERY branch's cacheable
    # prefix — including the PRD-grounded branch below, which skips corpus +
    # KG for cost reasons but must not go blind to uploads (that would
    # reintroduce the false-denial bug inside PRD-tab chat).
    docs_block, documents = document_grounding(
        enterprise_id, question, question_embedding=question_embedding
    )

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
        user = ASK_USER_TEMPLATE_QUESTION_ONLY.format(question=question)
        cacheable = prd_context
    else:
        corpus = load_corpus(dataset)
        cacheable = f"Source material:\n\n{corpus.joined()}" if corpus.docs else None

        bundle = _retrieve_kg_bundle(
            enterprise_id, question, question_embedding=question_embedding
        )

        if bundle:
            from app.graph.retrieval import render_context_section

            system = (ASK_SYSTEM + ASK_SYSTEM_KG_ADDENDUM + today_line()
                      + connected_sources_line(enterprise_id))
            user = ASK_USER_TEMPLATE_WITH_KG.format(
                kg_context=render_context_section(bundle), question=question
            )
        else:
            system = (ASK_SYSTEM + today_line()
                      + connected_sources_line(enterprise_id))
            user = ASK_USER_TEMPLATE_QUESTION_ONLY.format(question=question)

    # Self-reported workspace identity (interim incident fix): computed once
    # above so it rides EVERY branch's cacheable prefix, first — a long corpus
    # or PRD block can never push it out. facts == "" ⇒ cacheable/system are
    # byte-identical to the pre-fix composition (including the None case).
    # Documents sit after facts and before the corpus/PRD/KG block.
    cacheable = (
        "\n\n---\n\n".join(p for p in (facts, docs_block, cacheable) if p) or None
    )
    if facts:
        system += ASK_SYSTEM_COMPANY_FACTS_ADDENDUM
    if docs_block:
        system += ASK_SYSTEM_DOCUMENTS_ADDENDUM

    # Bind the tenant's own Claude key (when configured) for this direct
    # (non-gateway) answer call. See app.llm_keys.
    from app.llm_keys import company_llm_key

    with company_llm_key(enterprise_id):
        payload = call_json(
            system=system,
            user=user,
            user_cacheable_prefix=cacheable,
            schema=_ASK_RESPONSE_SCHEMA,
            max_tokens=12000,
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
