"""Turn a chat request about the backlog into concrete operations and questions.

The write half of the chat's backlog support (`backlog_context` is the read
half). The planner emits `backlog_action` with an `instruction` — "add dark
mode to the backlog", "mark the CSV export bug as done", "re-sequence by
impact" — and this module resolves that sentence against the LIVE backlog into:

  - `operations`: everything the request states unambiguously, ready to apply.
    Three kinds, matching the three things the Backlog screen itself can do:
    `add` (a new idea), `status` (move one idea to in progress / done /
    dismissed) and `reorder` (a new full ranking).
  - `questions`: everything it left open, shaped for the chat's QuestionPopup
    stepper — the SAME contract `ticket_assign` produces, so the chat renders
    both through one component. Each question carries the half-built operation
    it completes and names the field the answer fills, so the client can apply
    the result without a second round-trip to this module.
  - `note`: one line for anything not honoured.

WHY AN LLM AND NOT A MATCHER. "the export bug", "the mobile one", "the top
three" and "anything about billing" all name rows that carry none of those
words verbatim, and the ranking a re-sequence asks for ("push revenue items
up") is a judgement over titles. A matcher here would fail exactly on the
phrasings people actually use — the same reasoning `ticket_assign` records for
resolving a ticket and a person.

NOTHING IS WRITTEN HERE. This module reads and returns a plan; the client
applies it through the ordinary `/v1/ideation` routes the Backlog screen uses,
which is what keeps one write path (and one set of tenant checks) rather than
two. Never raises: a load failure, a model failure or a plan that validates
away all become a note the chat can say out loud.

EVERY ID IS VALIDATED against the ids that were actually read. A model that
invents an item id must not reach a PATCH, and a reorder that drops or
duplicates rows must not reach the reorder route — `reorder_ideation_items`
would happily persist a ranking missing half the backlog.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

_AGENT = "backlog"
PLAN_PROMPT_VERSION = "backlog-action-v1"

# Bound what rides into the prompt. The weekly pass shortlists 25–30 and manual
# adds are counted in ones, so this is a runaway guard rather than a ceiling.
_ITEM_CAP = 120
# One question per named idea is the worst normal case; past this a plan is a
# runaway generation, not a request.
_QUESTION_CAP = 20
# A title is a line on a list, not a brief.
_TITLE_CHARS = 200

#: The statuses a chat request may move an idea INTO. Mirrors
#: `db.ideation.PATCHABLE_STATUSES` — you do not re-set 'proposed' by hand.
_STATUSES = ("in_progress", "done", "dismissed")
#: The idea types, in the enum the API takes. Labels match the Backlog screen's
#: own (`IdeationScreen.tsx`), so the chat and the screen name a type alike.
_TAGS = ("something_broken", "something_new", "something_better")
_TAG_LABELS = {
    "something_broken": "Bug",
    "something_new": "New initiative",
    "something_better": "UI",
}

_SCHEMA: dict = {
    "type": "object",
    "properties": {
        # Reason first — the same generation-order rule every schema here
        # follows: the tokens explaining the plan exist before the plan.
        "reason": {"type": "string", "description": "One short clause."},
        "operations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["add", "status", "reorder"]},
                    "title": {
                        "type": ["string", "null"],
                        "description": "add only: the idea, as a short line.",
                    },
                    "tag": {
                        "type": ["string", "null"],
                        "enum": [*_TAGS, None],
                        "description": (
                            "add only: the type, when the request makes it "
                            "plain. Null when it does not — that becomes a "
                            "question, never a guess."
                        ),
                    },
                    "item_id": {
                        "type": ["string", "null"],
                        "description": "status only: the backlog item id, copied exactly.",
                    },
                    "status": {
                        "type": ["string", "null"],
                        "enum": [*_STATUSES, None],
                        "description": "status only.",
                    },
                    "ordered_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "reorder only: EVERY visible item id, in the new "
                            "order, highest priority first."
                        ),
                    },
                },
                "required": ["op"],
                "additionalProperties": False,
            },
        },
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "header": {
                        "type": "string",
                        "description": "2–3 word category chip, e.g. 'Which idea' or 'Type'.",
                    },
                    "prompt": {"type": "string"},
                    "fills": {
                        "type": "string",
                        "enum": ["item_id", "tag"],
                        "description": (
                            "Which field the answer fills: 'item_id' asks WHICH "
                            "idea (options are item ids), 'tag' asks what TYPE "
                            "a new idea is (options are the three types)."
                        ),
                    },
                    "op": {"type": "string", "enum": ["add", "status"]},
                    "title": {
                        "type": ["string", "null"],
                        "description": "The add's title, when this question completes an add.",
                    },
                    "status": {
                        "type": ["string", "null"],
                        "enum": [*_STATUSES, None],
                        "description": "The status to apply, when this question completes a status move.",
                    },
                    "option_item_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "fills=item_id: the candidate ideas.",
                    },
                    "multi": {
                        "type": "boolean",
                        "description": (
                            "True ONLY on an item_id question whose request "
                            "asks about MORE THAN ONE idea ('mark these done') "
                            "— the user then picks several at once."
                        ),
                    },
                },
                "required": ["prompt", "fills", "op"],
                "additionalProperties": False,
            },
        },
        "note": {
            "type": "string",
            "description": (
                "One line for anything not honoured (an idea that isn't on the "
                "backlog, a request you couldn't read). Empty when everything "
                "resolved."
            ),
        },
    },
    "required": ["operations", "questions"],
    "additionalProperties": False,
}

_SYSTEM = """You turn a chat request about a product BACKLOG into concrete \
operations for a product workspace. You are given the backlog exactly as it \
stands (id, rank, title, type, status) and the request itself.

The backlog is a ranked pool of product ideas. Rank is priority and LOWER IS \
HIGHER: #1 is the top idea. Three things can change: an idea can be ADDED, an \
idea can MOVE to in progress / done / dismissed, and the whole list can be \
RE-ORDERED.

Put in `operations` only what the request states unambiguously. Everything it \
leaves open becomes a `questions` entry the user answers with one tap — never \
a guess, and never a refusal.

RESOLVE BY MEANING, NOT BY STRING. "the export bug", "the mobile one", "the \
billing idea" name ideas whose titles may share no word with the phrase. Read \
the titles and decide. Copy `item_id` EXACTLY as given; never invent one.

WHEN A PHRASE FITS SEVERAL IDEAS, ASK — one question, `fills`="item_id", \
`option_item_ids` = the candidates, carrying the `op` and `status` that \
should apply once they pick. When it fits NONE, do not ask: say so in `note`.

ADDING. `title` is the idea as a short line in the user's own words — not a \
paragraph, not a brief. Set `tag` ONLY when the request makes the type plain: \
something_broken for a defect ("the export is broken", "fix the crash"), \
something_new for a new capability ("add SSO", "build a mobile app"), \
something_better for an improvement to something that already works ("make \
the dashboard faster", "tidy up the settings screen"). If the request does \
not make it plain, leave `tag` null and ask ONE question with \
`fills`="tag" carrying that title — do not invent a type. A request naming \
SEVERAL ideas to add becomes several `add` operations, each with its own \
title, and at most one type question each.

RE-ORDERING. `ordered_ids` must list EVERY visible id exactly once, in the new \
order. A request that moves a few ideas ("push the revenue ones up") still \
produces the FULL list — the movers in their new places and everything else \
in its existing relative order. A partial list is rejected and nothing is \
re-ordered. If the request names an ordering you cannot apply to these ideas, \
say so in `note` rather than shuffling arbitrarily.

DO NOT ANSWER QUESTIONS HERE. "what's on the backlog" is not a change and \
never reaches you; if the request turns out to be a question, return no \
operations and no questions, and say what it seems to be asking in `note`."""

_USER = """BACKLOG ({count} item(s), rank ascending = highest priority first):
{items}

REQUEST:
{instruction}"""

_EMPTY_NOTE = (
    "I couldn't work out what to change on the backlog — try naming the idea "
    "and what should happen to it, e.g. “mark the CSV export bug as done” or "
    "“add dark mode to the backlog”."
)


def _item_line(row: dict) -> str:
    tag = _TAG_LABELS.get(row.get("tag") or "", "untyped")
    return (
        f"- {row.get('id')} — #{row.get('rank')} — {row.get('title') or '(untitled)'} "
        f"— {tag} — {row.get('status') or 'proposed'}"
    )


def _clean_title(v) -> str:
    return str(v or "").strip()[:_TITLE_CHARS]


def plan_backlog_ops(enterprise_id: str, instruction: str) -> dict:
    """The validated plan for one backlog request. Never raises.

    Returns `{"operations": [...], "questions": [...], "note": str}`. An empty
    plan with a note is a normal outcome, not a failure: it is what the chat
    says when nothing matched.
    """
    try:
        from app.db.ideation import list_visible_ideation_items

        items = list_visible_ideation_items(enterprise_id) or []
    except Exception:  # noqa: BLE001 — a load failure is a note, not a 500
        logger.exception("backlog-action: load failed for %s", enterprise_id)
        return {"operations": [], "questions": [], "note": _EMPTY_NOTE}

    shown = items[:_ITEM_CAP]
    by_id = {str(r.get("id")): r for r in shown if r.get("id")}
    # The reorder validator compares against what the MODEL SAW, not against
    # the untrimmed read: asking for every id when the prompt was capped would
    # reject every re-sequence on a large backlog.
    all_ids = set(by_id)

    items_txt = "\n".join(_item_line(r) for r in shown) or "(the backlog is empty)"
    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent=_AGENT,
            purpose="backlog_action",
            system=_SYSTEM,
            input=_USER.format(
                count=len(shown), items=items_txt, instruction=instruction,
            ),
            prompt_version=PLAN_PROMPT_VERSION,
            json_schema=_SCHEMA,
            max_tokens=2500,
        )
        out = result.output if isinstance(result.output, dict) else {}
    except Exception:  # noqa: BLE001 — the chat must degrade, not error
        logger.exception("backlog-action: plan call failed for %s", enterprise_id)
        return {"operations": [], "questions": [], "note": _EMPTY_NOTE}

    dropped: list[str] = []
    operations: list[dict] = []
    for o in out.get("operations") or []:
        if not isinstance(o, dict):
            continue
        op = str(o.get("op") or "")
        if op == "add":
            title = _clean_title(o.get("title"))
            if not title:
                continue
            tag = o.get("tag")
            operations.append({
                "op": "add", "title": title,
                "tag": tag if tag in _TAGS else None,
            })
        elif op == "status":
            item_id = str(o.get("item_id") or "").strip()
            status = str(o.get("status") or "").strip()
            row = by_id.get(item_id)
            if row is None or status not in _STATUSES:
                dropped.append(item_id or "?")
                continue
            operations.append({
                "op": "status", "item_id": item_id, "status": status,
                "title": row.get("title") or "",
            })
        elif op == "reorder":
            ids = [str(i).strip() for i in (o.get("ordered_ids") or [])]
            # EXACTLY the same set, no duplicates. `reorder_ideation_items`
            # writes rank = position, so a list missing rows would silently
            # re-rank the backlog around the gaps.
            if len(ids) != len(all_ids) or set(ids) != all_ids:
                dropped.append("re-order")
                continue
            operations.append({"op": "reorder", "ordered_ids": ids})

    questions: list[dict] = []
    for q in (out.get("questions") or [])[:_QUESTION_CAP]:
        if not isinstance(q, dict):
            continue
        prompt = str(q.get("prompt") or "").strip()
        fills = str(q.get("fills") or "")
        op = str(q.get("op") or "")
        if not prompt or fills not in ("item_id", "tag") or op not in ("add", "status"):
            continue
        if fills == "item_id":
            # Options must be REAL ideas — a card offering an id that is not on
            # the backlog resolves to a PATCH that 404s after the user has
            # already picked.
            option_ids = [
                i for i in (str(x).strip() for x in (q.get("option_item_ids") or []))
                if i in by_id
            ]
            if len(option_ids) < 2:
                # Nothing to choose between: either the plan should have been an
                # operation, or the candidates validated away. Neither is a card.
                dropped.append(prompt[:40])
                continue
            status = str(q.get("status") or "").strip()
            if op == "status" and status not in _STATUSES:
                continue
            questions.append({
                "header": str(q.get("header") or "Which idea")[:40],
                "prompt": prompt,
                "fills": "item_id",
                "op": op,
                "status": status if op == "status" else None,
                "title": _clean_title(q.get("title")) or None,
                "multi": bool(q.get("multi")),
                "options": [
                    {
                        "value": i,
                        "label": (by_id[i].get("title") or "(untitled)")[:80],
                        "description": (
                            f"#{by_id[i].get('rank')} · "
                            f"{_TAG_LABELS.get(by_id[i].get('tag') or '', 'untyped')}"
                        ),
                    }
                    for i in option_ids
                ],
            })
        else:
            title = _clean_title(q.get("title"))
            if not title:
                continue
            questions.append({
                "header": str(q.get("header") or "Type")[:40],
                "prompt": prompt,
                "fills": "tag",
                "op": "add",
                "status": None,
                "title": title,
                # One idea has one type — a multi-pick here could only produce
                # a row that is a Bug and a UI change at once.
                "multi": False,
                "options": [
                    {"value": t, "label": _TAG_LABELS[t], "description": None}
                    for t in _TAGS
                ],
            })

    note = str(out.get("note") or "").strip()
    if dropped and not note:
        note = (
            "I skipped part of that — it named something that isn't on the "
            "backlog right now."
        )
    if not operations and not questions and not note:
        note = _EMPTY_NOTE
    logger.info(
        "backlog-action: company=%s ops=%d questions=%d dropped=%d",
        enterprise_id, len(operations), len(questions), len(dropped),
    )
    return {"operations": operations, "questions": questions, "note": note}
