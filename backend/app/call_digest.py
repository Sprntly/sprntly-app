"""On-demand customer-call digest — chat → Fireflies (live) → voice-of-customer-report.

When a user asks the chat to "summarize the customer calls from last week", the
generic Ask path answers it badly: KG retrieval is semantic + token-capped, so
"every call in a window" comes back sampled, and the VoC skill gets no real
corpus. This module runs the dedicated path instead:

  1. parse the time window from the question (default: last 7 days),
  2. fetch EVERY call in that window live from Fireflies — distilled summary plus
     a bounded sample of transient verbatim quotes (never persisted to the KG),
  3. assemble a complete corpus and run the voice-of-customer-report skill over
     it, so the answer has real counts, themes, and sourced quotes.

Intent detection (is_call_digest) lives in skill_router; qa_agent delegates here
when it fires. The window parser takes an injectable `now` so it stays testable.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.graph.gateway import llm_call
from app.kg_ingest.pullers.fireflies import CallTranscript, fetch_calls
from app.prompts import ASK_SYSTEM

logger = logging.getLogger(__name__)

_VOC_SKILL = "voice-of-customer-report"
ANSWER_MODEL = "claude-sonnet-4-6"
_DEFAULT_WINDOW_DAYS = 7
# Bound the corpus handed to the skill so a busy month of calls can't blow the
# context budget. Calls are newest-first; we keep the most recent under budget.
_CORPUS_CHAR_BUDGET = 80_000


@dataclass
class Window:
    since: datetime
    until: datetime
    label: str  # human phrase for the run line, e.g. "last week (Jun 16–22)"


@dataclass
class DigestCorpus:
    status: str                                    # ok | not_connected | no_calls | error
    window: Window
    calls: list[CallTranscript] = field(default_factory=list)
    text: str = ""
    error: str = ""

    @property
    def count(self) -> int:
        return len(self.calls)


# ── Window parsing ───────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _start_of_day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _fmt_range(since: datetime, until: datetime) -> str:
    """'Jun 16–22' or 'Jun 30 – Jul 2' for the run line."""
    if since.month == until.month:
        return f"{since:%b} {since.day}–{until.day}"
    return f"{since:%b} {since.day} – {until:%b} {until.day}"


def parse_window(question: str, *, now: datetime | None = None) -> Window:
    """Parse a time window from the question. Defaults to the last 7 days when no
    explicit window is named. `now` is injectable for deterministic tests."""
    now = now or _utc_now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    q = question.lower()

    # "last/past N days|weeks|months"
    m = re.search(r"\b(?:last|past|previous)\s+(\d{1,3})\s+(day|week|month)s?\b", q)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = n * {"day": 1, "week": 7, "month": 30}[unit]
        since = _start_of_day(now - timedelta(days=days))
        return Window(since, now, f"the last {n} {unit}{'s' if n != 1 else ''}")

    if "yesterday" in q:
        start = _start_of_day(now - timedelta(days=1))
        end = _start_of_day(now)
        return Window(start, end, f"yesterday ({start:%b %d})")

    if "today" in q:
        start = _start_of_day(now)
        return Window(start, now, f"today ({start:%b %d})")

    if "last week" in q or "past week" in q:
        # Previous calendar week, Monday–Sunday.
        this_monday = _start_of_day(now - timedelta(days=now.weekday()))
        since = this_monday - timedelta(days=7)
        until = this_monday
        return Window(since, until, f"last week ({_fmt_range(since, until - timedelta(days=1))})")

    if "this week" in q:
        since = _start_of_day(now - timedelta(days=now.weekday()))
        return Window(since, now, f"this week ({_fmt_range(since, now)})")

    if "last month" in q or "past month" in q:
        first_this = _start_of_day(now.replace(day=1))
        last_month_end = first_this
        prev = first_this - timedelta(days=1)
        since = _start_of_day(prev.replace(day=1))
        return Window(since, last_month_end, f"last month ({since:%B %Y})")

    if "this month" in q:
        since = _start_of_day(now.replace(day=1))
        return Window(since, now, f"this month ({since:%B %Y})")

    if "this quarter" in q or "last quarter" in q:
        q_start_month = 3 * ((now.month - 1) // 3) + 1
        this_q_start = _start_of_day(now.replace(month=q_start_month, day=1))
        if "last quarter" in q:
            prev = this_q_start - timedelta(days=1)
            since = _start_of_day(prev.replace(month=3 * ((prev.month - 1) // 3) + 1, day=1))
            return Window(since, this_q_start, f"last quarter ({since:%b}–{prev:%b %Y})")
        return Window(this_q_start, now, f"this quarter")

    # Default: rolling last 7 days.
    since = _start_of_day(now - timedelta(days=_DEFAULT_WINDOW_DAYS))
    return Window(since, now, f"the last {_DEFAULT_WINDOW_DAYS} days")


# ── Fetch + corpus assembly ──────────────────────────────────────────────────

def _load_api_key(company_id: str) -> str | None:
    """Decrypt the stored Fireflies API key for a company, or None if the source
    isn't connected / the credential can't be read."""
    from app import db

    row = db.get_connection(company_id, "fireflies")
    if not row:
        return None
    try:
        token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
    except (TokenEncryptionError, ValueError, KeyError, TypeError):
        logger.warning("call-digest: could not decrypt fireflies token for %s", company_id)
        return None
    return token_json.get("api_key") or None


def has_call_source(company_id: str) -> bool:
    """True when a live call source (Fireflies) is connected and its credential
    is readable — i.e. build_corpus can actually fetch calls. Lets the router
    divert a bare 'voice of customer' request to the live digest only when it
    will find data; with none connected, the caller falls through to the skill's
    what-to-connect guidance instead."""
    return _load_api_key(company_id) is not None


def _select_within_budget(calls: list[CallTranscript]) -> list[CallTranscript]:
    """Keep the most recent calls (input is newest-first) that fit the char
    budget, so a busy month can't blow the context. The first call is always
    kept even if it alone exceeds the budget."""
    selected: list[CallTranscript] = []
    size = 0
    for c in calls:
        block = len(c.render())
        if selected and size + block > _CORPUS_CHAR_BUDGET:
            break
        selected.append(c)
        size += block
    return selected


def build_corpus(company_id: str, window: Window) -> DigestCorpus:
    """Fetch every call in the window from Fireflies and assemble the corpus.

    Returns a DigestCorpus whose `status` tells the caller what happened:
    not_connected (no Fireflies), no_calls (window empty), error (API failed),
    or ok (corpus ready). Never raises — the chat answer degrades gracefully."""
    api_key = _load_api_key(company_id)
    if not api_key:
        return DigestCorpus(status="not_connected", window=window)
    try:
        calls = fetch_calls(api_key, since=window.since, until=window.until)
    except Exception as e:  # noqa: BLE001 — surface as a graceful chat message
        logger.warning("call-digest: fireflies fetch failed for %s: %s", company_id, e)
        return DigestCorpus(status="error", window=window, error=str(e))
    if not calls:
        return DigestCorpus(status="no_calls", window=window)
    selected = _select_within_budget(calls)
    text = "\n\n".join(c.render() for c in selected)
    return DigestCorpus(status="ok", window=window, calls=selected, text=text)


# ── Answer assembly ──────────────────────────────────────────────────────────

def _plain_payload(answer: str, *, confidence: float = 0.0) -> dict:
    """An Ask-shaped payload for the non-LLM branches (not connected / no calls /
    error), tagged so the UI attributes it to the call-digest path."""
    return {
        "answer": answer, "key_points": [], "citations": [],
        "confidence": confidence, "unanswered": "",
        "_skill": _VOC_SKILL, "_skill_action": "Summarize customer calls",
        "_skill_source": "call-digest",
    }


def answer(*, enterprise_id: str, question: str, history: list[dict] | None = None) -> dict:
    """Run the on-demand call digest and return an Ask-shaped payload.

    Parses the window, fetches the calls live, and — when there are calls — runs
    voice-of-customer-report over the complete corpus. Connection/empty/error
    cases return a helpful plain message instead."""
    # Imported lazily to avoid a module-load cycle (ask_runner → qa_agent → ...).
    from app.ask_runner import _ASK_RESPONSE_SCHEMA

    window = parse_window(question)
    corpus = build_corpus(enterprise_id, window)

    if corpus.status == "not_connected":
        return _plain_payload(
            "I can summarize your customer calls, but no call source is connected "
            "yet. Connect **Fireflies** in Settings → Connectors (paste your "
            "Fireflies API key) and I'll pull the transcripts and synthesize them "
            "into a voice-of-customer report."
        )
    if corpus.status == "error":
        return _plain_payload(
            f"I couldn't reach Fireflies to pull your calls for {window.label} "
            "just now. Please retry in a moment — if it keeps failing, your "
            "Fireflies API key may need reconnecting in Settings → Connectors."
        )
    if corpus.status == "no_calls":
        return _plain_payload(
            f"No customer calls found in Fireflies for {window.label}. Try a wider "
            "window (e.g. \"summarize calls from the last 30 days\"), or check that "
            "your meetings are syncing to Fireflies."
        )

    # status == ok → run the VoC skill over the complete corpus.
    system = (
        ASK_SYSTEM
        + "\n\nThe user asked you to summarize their customer calls. Follow the "
        f"'{_VOC_SKILL}' skill's method over the call transcripts provided below "
        "to produce a voice-of-customer report. These are curated, first-party "
        "recorded calls (direct access to the user). Use ONLY the calls provided; "
        "every quote must be real and sourced to a call; never invent counts or "
        f"quotes. The window is {window.label} and there are {corpus.count} call(s)."
    )
    user = (
        (_render_history(history))
        + f"Question: {question}\n\n"
        f"=== CUSTOMER CALLS — {window.label} ({corpus.count} calls) ===\n"
        + corpus.text
    )
    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent="qa",
            purpose="call_digest",
            model=ANSWER_MODEL,
            system=system,
            input=user,
            prompt_version="qa-call-digest-v1",
            json_schema=_ASK_RESPONSE_SCHEMA,
            skill=_VOC_SKILL,
            max_tokens=12000,
        )
        payload = (
            result.output if isinstance(result.output, dict)
            else {"answer": str(result.output), "key_points": [], "citations": [],
                  "confidence": 0.5, "unanswered": ""}
        )
    except Exception:  # noqa: BLE001 — never break the chat
        logger.exception("call-digest: VoC skill run failed for %s", enterprise_id)
        return _plain_payload(
            f"I pulled {corpus.count} call(s) for {window.label} but hit an error "
            "synthesizing the report. Please retry."
        )

    payload["_skill"] = _VOC_SKILL
    payload["_skill_action"] = f"Voice of customer · {corpus.count} calls · {window.label}"
    payload["_skill_source"] = "call-digest"
    return payload


def _render_history(history: list[dict] | None) -> str:
    if not history:
        return ""
    recent = history[-6:]
    rows = [f"{t.get('role', 'user').capitalize()}: {t.get('content', '')}" for t in recent]
    return "Conversation so far:\n" + "\n".join(rows) + "\n\n"
