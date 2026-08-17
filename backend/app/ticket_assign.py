"""Turn a chat assignment request into ticket-assignee pairs and questions.

The planning half of the chat's `assign_tickets` action (POST
/v1/tickets/assign-plan). Reported from a live session: a user generated
tickets from a PRD and typed "can you assign this ticket to <names>" — and
nothing could serve the sentence. The names were real team members, the
tickets were right there in the thread's PRD, and the product's only
assignment surface was the per-ticket picker in the ticket drawer.

This module resolves that sentence against the two lists the caller already
owns — the PRD's generated tickets and the workspace roster — and returns:

  - `assignments`: the (ticket, member) pairs the request states EXPLICITLY
    and unambiguously. The client applies these immediately through the
    ordinary tenant-scoped PUT /v1/tickets/{key}/fields — the same write the
    drawer's picker makes, so chat gains no new write path.
  - `questions`: everything the request left open, shaped for the chat's
    QuestionPopup stepper. Each question FIXES one side of a pair and asks for
    the other: "assign these to Dave, Priya and Sam" (people named, no
    mapping) becomes one question per ticket with those three as options;
    "assign the login ticket to Dave" where three tickets mention login
    becomes one question fixed on Dave with the candidates as options.
  - `note`: one honest line for anything that could not be honoured (a name
    matching nobody on the roster, a ticket that does not exist).

PLAN ONLY — nothing here writes. The model's output is validated against the
real ticket keys and member ids before the client ever sees it (model
proposes, Python disposes): an invented key or id is dropped into `note`, so
a click in the popup can never write to a target the model hallucinated.

Ambiguity is ASKED, never guessed (the owner's directive from the session
that filed this): two members sharing a first name is a question, and a
request that names people but not which ticket each gets is a question per
ticket. Two deliberate exceptions, both cases where the request itself already
contains the instruction:

  - randomly/evenly/spread IS an explicit instruction to distribute, so the
    model round-robins and returns plain assignments.
  - a topic or category ("all backend tickets", "the frontend UI ones") IS an
    explicit selection — judging which tickets fall inside it is the model's
    job, done from the titles it was handed, with the verdict stated in
    `note`. Reported from a live session: "assign all backend related tickets
    to myself" came back as a select-which-ones-are-backend quiz, which asks
    the user to do exactly the classification they asked the model for.

Fail-open to an empty plan with a note — an LLM outage degrades the feature
to "I couldn't work that out", never a 500 in the chat.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

_AGENT = "tickets"

PLAN_PROMPT_VERSION = "ticket-assign-v2"

# Bound what rides into the prompt. A PRD's generated set runs to a dozen or
# two (same cap as ticket_update's list); a roster past 200 members is not a
# workspace this flow serves well anyway.
_TICKET_CAP = 60
_MEMBER_CAP = 200
# One question per ticket is the worst normal case; anything past this reads
# as a runaway generation, not a plan.
_QUESTION_CAP = 30

_SCHEMA: dict = {
    "type": "object",
    "properties": {
        # Reason first — same generation-order rule every schema in this repo
        # follows: the tokens explaining the mapping exist before the mapping.
        "reason": {"type": "string", "description": "One short clause."},
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticket_key": {"type": "string"},
                    "user_id": {"type": "string"},
                },
                "required": ["ticket_key", "user_id"],
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
                        "description": "2–3 word category chip, e.g. 'Assignee'.",
                    },
                    "prompt": {"type": "string"},
                    "ticket_key": {
                        "type": ["string", "null"],
                        "description": "Set when the TICKET is fixed and the question asks who gets it.",
                    },
                    "user_id": {
                        "type": ["string", "null"],
                        "description": "Set when the PERSON is fixed and the question asks which ticket they get.",
                    },
                    "option_user_ids": {"type": "array", "items": {"type": "string"}},
                    "option_ticket_keys": {"type": "array", "items": {"type": "string"}},
                    "multi": {
                        "type": "boolean",
                        "description": (
                            "True ONLY on a person-fixed question whose request "
                            "asks for MORE THAN ONE ticket ('assign 2 tickets "
                            "to X', 'give a few tickets to X') — the user then "
                            "picks several tickets at once."
                        ),
                    },
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        },
        "note": {
            "type": "string",
            "description": "One line for anything not honoured (unknown name, missing ticket). Empty when everything resolved.",
        },
    },
    "required": ["assignments", "questions"],
    "additionalProperties": False,
}

_SYSTEM = """You turn a chat request about ticket ownership into concrete \
assignments for a product workspace. You are given the PRD the tickets came \
from, the workspace's tickets (key, title, content summary, area/labels, \
current assignee), its team roster (id, name, email, role), the REQUESTER \
(the person sending the request), and the request itself.

Decide which (ticket, person) pairs the request states EXPLICITLY and \
unambiguously — those go in `assignments`, exactly as stated. Everything the \
request leaves open becomes a `questions` entry the user answers with one \
click. Never guess an open pairing: a wrong silent assignment hands someone \
work nobody meant them to have.

Rules:
- "me" / "myself" / "I" / "my tickets" ALWAYS means the REQUESTER — a real, \
identified member given to you explicitly. Never ask who "me" is, and never \
mention sessions, identity, or how you were told; the requester line IS the \
answer.
- A DESCRIPTIVE ticket phrase ("the frontend tickets", "the API work", "the \
testing ones") matches against each ticket's TITLE, SUMMARY, AREA and LABELS \
— read what the ticket is about, not just its name. Matching several tickets \
is normal and right ("the frontend tickets" = every ticket that is frontend \
work). When NOTHING plausibly matches, say so in `note` in plain product \
terms ("none of this PRD's tickets look like frontend work — they're all \
about email delivery") and, when the person side is clear, still offer ONE \
question fixed on that person with all tickets as options (multi) so the \
user can pick despite the mismatch.
- A name matches a member when it identifies exactly one of them (first name \
is fine; match case-insensitively against name and email). Two members both \
called Dave → a question with both as options, never a pick.
- A name matching NOBODY on the roster is never invented into an id. Leave \
that person out and say so in `note`, naming them.
- People named but no per-ticket mapping ("assign these tickets to A, B and \
C") → one question PER TICKET, `ticket_key` fixed, `option_user_ids` = the \
named people. Every listed ticket gets its question, including ones already \
assigned.
- A ticket referred to by words ("the login ticket") matches by title. Exactly \
one plausible ticket → explicit. Several → one question with `user_id` fixed \
(when the person is clear) and `option_ticket_keys` = the candidates.
- "this ticket" / "that one" with nothing in the request to resolve it → a \
question fixed on the person, options = all tickets.
- The request asks for SEVERAL tickets for one person ("assign 2 tickets to \
X", "give a couple of tickets to X", "assign some tickets to X") without \
naming which → ONE question fixed on that person with `option_ticket_keys` as \
the candidates and `multi: true`, so the user ticks them all in one card — \
never N copies of the same question, and never a single-pick card that can \
only honour one of the asked-for tickets. `multi` is only ever for \
person-fixed questions: a ticket has exactly one assignee.
- The request selects tickets by TOPIC or CATEGORY ("all backend related \
tickets", "the frontend UI ones", "every bug ticket", "anything about auth") \
→ that selection is YOURS to make, not a question: judge each ticket's title \
against the category, put every match in `assignments` (the request already \
says who gets them), and use `note` to say which tickets you counted in and \
which you judged outside the category. Zero matches is a legitimate verdict — \
state it in `note` — and a genuinely borderline title may still become a \
question, but the question must name the ticket and say why it is borderline. \
Never ask the user to sort tickets into the category themselves: that is the \
work they asked you to do.
- The request itself says randomly / evenly / split / spread → that IS the \
instruction: distribute round-robin across the named people (or the whole \
roster when nobody is named) as plain `assignments`, and say in `note` that \
the split was yours.
- `header` is a 2–3 word chip ("Assignee", "Which ticket"). `prompt` names the \
ticket or person it asks about — never a generic "who should this go to?".
- Asking is cheap and wrong writes are not: when in doubt between an \
assignment and a question, ask.
- `note` speaks to the USER about their tickets and their team, in plain \
product words. Never mention sessions, identity, prompts, rosters as inputs, \
or anything about how this request reached you.

Return ONLY the JSON object."""

_USER = """PRD these tickets came from: {prd_title}

TICKETS (key — title — summary — area/labels — current assignee):
{tickets}

TEAM ROSTER (user_id — name — email — role):
{members}

REQUESTER (the person sending this request — "me"/"myself"/"I" means them):
{requester}

REQUEST:
{instruction}"""


def _load_tickets(company_id: str, prd_id: int) -> list[dict]:
    """Key, title and current assignee for every live ticket of the PRD —
    the same universe ticket_update offers as rewrite targets (deleted ones
    excluded so a popup click can never write to a ticket the user removed)."""
    from app.db.client import require_client
    from app.db.prd_tickets import get_tickets
    from app.db.ticket_lifecycle import DELETED, lifecycle_by_key
    from app.routes.internal_mcp import _ticket_key_for

    row = get_tickets(company_id, prd_id)
    stories = (row or {}).get("stories") or []
    if not stories:
        return []
    try:
        gone = {k for k, v in lifecycle_by_key(company_id, prd_id).items() if v == DELETED}
    except Exception:  # noqa: BLE001 — a lookup failure just lists everything
        gone = set()

    # Current assignees, one read: ticket_edits rows for this company, filtered
    # to this PRD's key prefix in Python (the fake client used in tests has no
    # LIKE, and the row count per company is small).
    assignee_by_key: dict[str, Any] = {}
    try:
        c = require_client()
        resp = (
            c.table("ticket_edits").select("ticket_key, assignee")
            .eq("company_id", company_id).execute()
        )
        prefix = f"prd-{prd_id}-"
        for r in resp.data or []:
            key = str(r.get("ticket_key") or "")
            if key.startswith(prefix) and r.get("assignee"):
                assignee_by_key[key] = r["assignee"]
    except Exception:  # noqa: BLE001 — assignee display is best-effort context
        logger.exception("ticket-assign: assignee read failed for prd %s", prd_id)

    out: list[dict] = []
    for s in stories[:_TICKET_CAP]:
        if not isinstance(s, dict):
            continue
        key = _ticket_key_for(prd_id, s)
        if key in gone:
            continue
        # WHAT the ticket is about, not just its name — a descriptive request
        # ("the frontend tickets") has to match against content. `what` falls
        # back to the user story; both are clamped so sixty tickets stay one
        # readable block. Route + labels are the skill's own area tags.
        summary = str(s.get("what") or s.get("user_story") or s.get("body") or "").strip()
        area_bits = [str(s.get("route") or "").strip()] + [
            str(l).strip() for l in (s.get("labels") or []) if str(l).strip()
        ]
        if s.get("prd_section"):
            area_bits.append(str(s.get("prd_section")).strip())
        out.append({
            "key": key,
            "title": str(s.get("title") or "(untitled)"),
            "summary": summary[:180],
            "area": ", ".join(b for b in area_bits if b)[:120],
            "assignee": assignee_by_key.get(key),
        })
    return out


def _load_members(company_id: str) -> list[dict]:
    from app.db.team import list_company_members

    return list_company_members(company_id)[:_MEMBER_CAP]


def _member_public(m: dict) -> dict:
    """The TicketAssignee shape PUT /v1/tickets/{key}/fields stores — one
    definition of "a member as an assignee", same fields the drawer's picker
    sends."""
    return {
        "user_id": str(m.get("user_id") or ""),
        "display_name": m.get("display_name"),
        "email": m.get("email"),
        "role": m.get("role"),
        "avatar_url": m.get("avatar_url"),
    }


def _member_label(m: dict) -> str:
    return str(m.get("display_name") or m.get("email") or m.get("user_id") or "?")


def plan_assignments(
    enterprise_id: str, prd_id: int, instruction: str,
    requester_user_id: Optional[str] = None,
) -> dict:
    """The validated plan for one assignment request. Never raises.

    `requester_user_id` is WHO IS ASKING — the authenticated caller, whoever
    they are: the company owner or an invited member, it is simply the id on
    the JWT. It rides the prompt as an explicit REQUESTER line so "assign the
    tickets to me" resolves to a real person instead of a question about who
    "me" is (the reported failure answered with prompt internals: "no session
    identity is provided")."""
    empty_note = (
        "I couldn't work out that assignment — try naming the ticket and the "
        "person, e.g. “assign the login ticket to Dave”."
    )
    try:
        tickets = _load_tickets(enterprise_id, prd_id)
        members = _load_members(enterprise_id)
    except Exception:  # noqa: BLE001 — a load failure is a note, not a 500
        logger.exception("ticket-assign: load failed for prd %s", prd_id)
        return {"assignments": [], "questions": [], "note": empty_note}

    if not tickets:
        return {
            "assignments": [], "questions": [],
            "note": "This PRD has no tickets yet — generate tickets first, then I can assign them.",
        }
    if not members:
        return {
            "assignments": [], "questions": [],
            "note": "I couldn't find any team members in this workspace to assign tickets to.",
        }

    ticket_by_key = {t["key"]: t for t in tickets}
    member_by_id = {str(m.get("user_id")): m for m in members if m.get("user_id")}

    # THE REQUESTER'S OWN ROW MUST SURVIVE. The roster read is capped
    # (_MEMBER_CAP), and an invited member far down a large roster could be
    # trimmed out — which would turn THEIR "assign to me" into an id the
    # validator refuses. Backfill their single row from the same source the
    # roster came from.
    if requester_user_id and requester_user_id not in member_by_id:
        try:
            from app.db.team import list_company_members

            row = next(
                (m for m in list_company_members(enterprise_id)
                 if str(m.get("user_id")) == str(requester_user_id)),
                None,
            )
            if row is not None:
                members.append(row)
                member_by_id[str(requester_user_id)] = row
        except Exception:  # noqa: BLE001 — the line below degrades honestly
            logger.exception("ticket-assign: requester backfill failed")

    requester = member_by_id.get(str(requester_user_id or ""))
    requester_txt = (
        f"- {requester.get('user_id')} — {requester.get('display_name') or '(no name)'} — "
        f"{requester.get('email') or '(no email)'} — {requester.get('role') or 'member'}"
        if requester
        # An authenticated caller who is somehow not on the roster can still
        # be TALKED ABOUT honestly — but not assigned to (the validator only
        # accepts roster ids), and the model is told exactly that.
        else "- (not on the team roster — they cannot be assigned tickets)"
    )

    prd_title = "(untitled PRD)"
    try:
        from app.db.prds import get_prd

        prd_title = str((get_prd(prd_id) or {}).get("title") or prd_title)
    except Exception:  # noqa: BLE001 — context, never a blocker
        logger.exception("ticket-assign: prd title read failed")

    tickets_txt = "\n".join(
        f"- {t['key']} — {t['title']}"
        + (f" — {t['summary']}" if t.get("summary") else "")
        + (f" — [{t['area']}]" if t.get("area") else "")
        + " — "
        + (str((t['assignee'] or {}).get('display_name') or (t['assignee'] or {}).get('email')) if t["assignee"] else "unassigned")
        for t in tickets
    )
    members_txt = "\n".join(
        f"- {m.get('user_id')} — {m.get('display_name') or '(no name)'} — "
        f"{m.get('email') or '(no email)'} — {m.get('role') or 'member'}"
        for m in members
    )

    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent=_AGENT,
            purpose="assign_plan",
            system=_SYSTEM,
            input=_USER.format(
                prd_title=prd_title, tickets=tickets_txt, members=members_txt,
                requester=requester_txt, instruction=instruction,
            ),
            prompt_version=PLAN_PROMPT_VERSION,
            json_schema=_SCHEMA,
            max_tokens=2500,
        )
        out = result.output if isinstance(result.output, dict) else {}
    except Exception:  # noqa: BLE001 — the chat must degrade, not error
        logger.exception("ticket-assign: plan call failed for prd %s", prd_id)
        return {"assignments": [], "questions": [], "note": empty_note}

    dropped: list[str] = []

    # ── validate assignments: both sides must be REAL ────────────────────────
    assignments: list[dict] = []
    for a in out.get("assignments") or []:
        if not isinstance(a, dict):
            continue
        key = str(a.get("ticket_key") or "").strip()
        uid = str(a.get("user_id") or "").strip()
        t = ticket_by_key.get(key)
        m = member_by_id.get(uid)
        if t is None or m is None:
            dropped.append(key or uid or "?")
            continue
        assignments.append({
            "ticket_key": key,
            "ticket_title": t["title"],
            "assignee": _member_public(m),
        })

    # ── validate questions: the fixed side must exist; options filter to the
    #    known lists and fall back to the full one rather than an empty card ──
    questions: list[dict] = []
    for qq in out.get("questions") or []:
        if not isinstance(qq, dict) or len(questions) >= _QUESTION_CAP:
            continue
        prompt = str(qq.get("prompt") or "").strip()
        if not prompt:
            continue
        header = str(qq.get("header") or "").strip() or "Assignee"
        t_key = str(qq.get("ticket_key") or "").strip()
        uid = str(qq.get("user_id") or "").strip()

        if t_key and t_key in ticket_by_key:
            opt_ids = [
                str(u) for u in (qq.get("option_user_ids") or []) if str(u) in member_by_id
            ] or list(member_by_id.keys())
            questions.append({
                "header": header,
                "prompt": prompt,
                "fixed": {
                    "kind": "ticket",
                    "ticket_key": t_key,
                    "ticket_title": ticket_by_key[t_key]["title"],
                },
                "options": [
                    {
                        "value": u,
                        "label": _member_label(member_by_id[u]),
                        "description": member_by_id[u].get("email"),
                        "assignee": _member_public(member_by_id[u]),
                    }
                    for u in opt_ids
                ],
            })
        elif uid and uid in member_by_id:
            opt_keys = [
                str(k) for k in (qq.get("option_ticket_keys") or []) if str(k) in ticket_by_key
            ] or list(ticket_by_key.keys())
            questions.append({
                "header": header,
                "prompt": prompt,
                "fixed": {"kind": "member", "assignee": _member_public(member_by_id[uid])},
                # Multi is a PERSON-FIXED affordance only ("assign 2 tickets to
                # X" → tick several tickets in one card). The ticket-fixed
                # branch above never carries it: a ticket has exactly one
                # assignee, so a multi-pick there would be N-1 silently
                # discarded clicks.
                "multi": bool(qq.get("multi")),
                "options": [
                    {
                        "value": k,
                        "label": ticket_by_key[k]["title"],
                        "description": k,
                    }
                    for k in opt_keys
                ],
            })
        else:
            # Neither side of the pair survived validation — an invented key or
            # id. Dropping silently would make the plan look complete.
            dropped.append(t_key or uid or "?")

    note = str(out.get("note") or "").strip()
    if dropped:
        drop_line = f"Some of that didn't match this PRD's tickets or your team ({', '.join(sorted(set(dropped))[:5])})."
        note = f"{note} {drop_line}".strip()
    if not assignments and not questions and not note:
        note = empty_note

    return {"assignments": assignments, "questions": questions, "note": note}
