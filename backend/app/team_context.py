"""The company's own TEAM — who is in this workspace, rendered for an answer.

Asked for by the planner (`ask_planner.Plan.include_team`) and executed on the
answer path, this is what makes "who's on my team", "what's Dave's role" and
"what's the designer's email" answerable at all. Before it, those questions
reached a model holding the company's knowledge graph and its connected
sources — and NOTHING that says who works here. The honest outcome was "I
don't have access to your team"; the dishonest one was a roster assembled from
whoever happened to be quoted in a synced Slack thread or assigned a Jira
issue, which is a list of people who touched a tool, not a list of members.

ONE READ, `db.team.list_company_members` — the same rows the Settings → Team
screen shows, so the chat and that screen can never disagree. Scoped by
COMPANY, like the library block and unlike a dataset: membership is a company
fact, and a person in another workspace of the same company is still a
colleague.

TWO DIFFERENT THINGS ARE BOTH CALLED "ROLE" and the block labels which is
which, because the answer has to: `job_role` is the job designation picked at
onboarding (Founder / PM / Engineer / Data Scientist / Designer), and `role`
is the Sprntly permission level (owner / admin / member / viewer). "Who are
our engineers" is the first; "who can invite people" is the second.

Never raises, and returns "" for a read that failed — a block that said "you
have no team" because a query timed out would be a confident lie about the
user's own data, and no block at all degrades to the answer they got before
this existed. An EMPTY company (no members but a successful read) is a real
state and does render, because a solo workspace saying so plainly is a true
answer.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# The whole roster reaches the prompt, up to this bound. Same number, and the
# same reasoning, as `ticket_assign._MEMBER_CAP`: a roster past 200 is not a
# team anyone is asking a chat question about. Truncation is DECLARED in the
# block (see below) rather than silent — a clipped list presented as complete
# is the one failure this block exists to prevent.
_MAX_MEMBERS = 200

_SETTINGS_SCREEN = "Settings → Team"


def _member_line(m: dict) -> str:
    """One roster row, every field always present.

    A missing value renders as "(none set)" rather than dropping the field:
    the shape of the line is what tells the model these are the four things it
    knows about a person, and a line that silently loses its email reads as a
    person who has none.
    """
    name = (m.get("display_name") or "").strip() or "(no name set)"
    email = (m.get("email") or "").strip() or "(no email on file)"
    job = (m.get("job_role") or "").strip() or "(no job role set)"
    access = (m.get("role") or "").strip() or "(no access level set)"
    return f"- {name} — {email} — job: {job} — access: {access} — user id: {m.get('user_id')}"


def team_block(company_id: Optional[str]) -> str:
    """This company's members, as a context section for the answer."""
    if not company_id:
        return ""
    try:
        from app.db.team import list_company_members

        members = list_company_members(company_id) or []
    except Exception:  # noqa: BLE001 — an unreadable roster degrades, never lies
        logger.exception("team block: member read failed for %s", company_id)
        return ""

    # Stable ordering: the same question asked twice must not produce two
    # differently-ordered lists, which reads as the assistant looking at
    # different data each time (the library block's `_KIND_ORDER` says the
    # same). Name first, email as the tiebreaker for the unnamed.
    members.sort(key=lambda m: (
        (m.get("display_name") or "").lower(),
        (m.get("email") or "").lower(),
    ))
    shown, dropped = members[:_MAX_MEMBERS], max(0, len(members) - _MAX_MEMBERS)

    parts = [
        "=== THIS WORKSPACE'S TEAM ===",
        "This is the complete, current list of the people in this company's "
        "Sprntly workspace, read just now from Sprntly's own records. It is "
        "authoritative: if someone is not here, they are not a member. Never "
        "name a teammate who does not appear below.",
        # The same collision the library block names for "template", for the
        # same reason: connected sources are full of people, and none of them
        # are members by virtue of appearing there.
        "A person who shows up in a synced Slack message, a Jira assignee "
        "field, a call transcript or a wiki page is NOT a member of this "
        "workspace unless they are listed here.",
        # The follow-up that exposed this: "a table of each member and the
        # number of PRDs they created". Sprntly records no author on what it
        # generates, so the table cannot be built — but the model, holding the
        # roster and the document index and nothing else, went looking for
        # PRDs in the SYNCED sources and reported "none retrieved from
        # Confluence, Google Drive, Jira or uploads" to a workspace with
        # twelve of its own. Wrong twice: the count is unanswerable, and the
        # place it looked is not where a Sprntly PRD lives.
        "WHAT THIS LIST CANNOT BE JOINED TO: Sprntly does not record an "
        "author on the documents it generates. A PRD, ticket set, prototype "
        "or report belongs to the workspace, not to the person who asked for "
        "it — most are generated from an insight rather than written by "
        "anyone. So a per-member count of PRDs or any other artifact CANNOT "
        "be produced. Say that plainly, and say why. Never answer such a "
        "question by searching the connected sources, and never report that "
        "the workspace has no PRDs — it has its own, listed on the Artifacts "
        "screen or by asking for them, they simply carry no author.",
        "Each line is: name — email — job (what they do) — access (their "
        "Sprntly permission level: owner/admin/member/viewer) — user id (an "
        "internal identifier; mention it only if asked for it).",
        "",
        f"MEMBERS ({len(members)}). Managed on the {_SETTINGS_SCREEN} screen.",
    ]
    parts.extend([_member_line(m) for m in shown] or [
        "(No members on file — this workspace has nobody in it yet, which is "
        "unusual enough to be worth saying plainly.)"
    ])
    if dropped:
        parts.append(
            f"(+{dropped} more members not shown — say the list was truncated "
            f"at {_MAX_MEMBERS} rather than presenting it as complete.)"
        )
    return "\n".join(parts)
