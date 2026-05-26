"""On-Call Agent — emergency brief generator.

Given a single support ticket (optionally part of a cluster), call the
LLM with the workspace KPI tree as context and produce a structured
EmergencyBrief. The LLM is run through `app.llm.call_json` so the same
mocking / caching infrastructure as the rest of the app applies; tests
swap the function via the `fake_llm` fixture.

Hard invariant: the returned brief ALWAYS has
`proposed_solution.requires_pm_approval == True`. Even if the LLM
hallucinates `false`, the runner re-stamps it to True before handing
back. V1 NEVER auto-acts — every downstream surface (PM notification
routing, Claude Code handoff, ticket creation) is gated on a manual PM
click.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.corpus import load_corpus
from app.llm import call_json
from app.oncall.models import EmergencyBrief, TicketInput

logger = logging.getLogger(__name__)


# Bumped on prompt edits so cached briefs (if we ever cache them) can be
# invalidated the same way BRIEF_SCHEMA_VERSION + friends are. Kept in
# this module rather than app.prompts to avoid touching shared state in
# the V1 patch.
EMERGENCY_BRIEF_SCHEMA_VERSION = 1


EMERGENCY_BRIEF_SYSTEM = """\
You are an on-call SRE agent for a SaaS product. A product manager has \
escalated a support ticket (or a cluster of related tickets) to you. \
Analyze the ticket text against the workspace's product context (KPI \
tree, product type) and produce a structured emergency brief.

The brief MUST include:
  1. A 1-line restatement of the ticket (ticket_summary).
  2. A root cause hypothesis with confidence (low/medium/high) and the \
specific evidence from the ticket text that supports it.
  3. An impact assessment: an estimate of affected users, severity \
(low/medium/high/critical), and which product features are touched.
  4. A proposed solution with a 1-line summary, an optional code-change \
hint ("fix X in Y, change Z logic in service W"), and an optional \
rollback plan.
  5. Agent notes: caveats, missing context, anything you wanted to know \
but couldn't infer from the ticket.

Hard rules:
  - NEVER recommend auto-acting. A human PM must approve every \
follow-up step. `requires_pm_approval` is ALWAYS true.
  - Ground every claim in the ticket text or the workspace context. Do \
not invent user counts, do not invent error codes, do not invent \
service names.
  - If the ticket text is too thin to support a confident hypothesis, \
say so in `agent_notes` and set `root_cause.confidence` to "low".
  - Output JSON ONLY. No prose outside the JSON, no markdown fences."""


EMERGENCY_BRIEF_USER_TEMPLATE = """\
Workspace: {workspace_id}

Workspace product context (KPI tree / corpus):
<<< BEGIN WORKSPACE CONTEXT >>>
{workspace_context}
<<< END WORKSPACE CONTEXT >>>

Incoming ticket:
  - source: {source}
  - ticket_id: {ticket_id}
  - title: {title}
  - reporter: {reporter}
  - received_at: {received_at}
  - linked_ticket_ids: {linked_ticket_ids}

Ticket body:
<<< BEGIN TICKET BODY >>>
{body}
<<< END TICKET BODY >>>

Return JSON with this exact shape:

{{
  "ticket_summary": "<1-line restatement of the ticket>",
  "root_cause": {{
    "summary": "<1-line hypothesis>",
    "confidence": "low" | "medium" | "high",
    "evidence": ["<bullet>", "<bullet>", ...]
  }},
  "impact": {{
    "affected_user_count_estimate": <int or null>,
    "severity": "low" | "medium" | "high" | "critical",
    "affected_features": ["<feature>", ...]
  }},
  "proposed_solution": {{
    "summary": "<1-line proposed fix>",
    "code_change_hint": "<optional, e.g. 'fix X in Y, change Z logic in service W'>",
    "rollback_plan": "<optional rollback plan>",
    "requires_pm_approval": true
  }},
  "agent_notes": "<caveats, missing context, anything you'd want a PM to know>"
}}"""


def _load_workspace_context(workspace_id: str) -> str:
    """Return the workspace's KPI tree / corpus as a string for prompt
    embedding. For V1 we reuse `app.corpus.load_corpus` — workspace_id
    is interpreted as the dataset slug (the only workspace concept the
    backend currently has). If the workspace has no on-disk corpus, we
    return a short placeholder rather than raising: the on-call agent
    should still produce a brief from the ticket text alone, just with
    lower confidence.
    """
    try:
        corpus = load_corpus(workspace_id)
        return corpus.joined()
    except (FileNotFoundError, RuntimeError) as exc:
        logger.warning(
            "No corpus for workspace %r (%s); on-call brief will run "
            "ticket-text-only",
            workspace_id,
            exc,
        )
        return (
            f"(No KPI tree / product corpus is registered for workspace "
            f"{workspace_id!r}. Analyze the ticket on its own and flag "
            f"the missing context in agent_notes.)"
        )


def generate_emergency_brief(
    workspace_id: str,
    ticket_input: TicketInput,
) -> EmergencyBrief:
    """Run the on-call LLM and return a parsed EmergencyBrief.

    Invariants enforced here (defense in depth — the model also defaults
    `requires_pm_approval=True`):
      - `proposed_solution.requires_pm_approval` is forced to True on
        the returned brief. V1 NEVER auto-acts.
      - `workspace_id` and `generated_at` on the returned brief come
        from this function, not the LLM, so the caller's identity and
        the server clock are authoritative.
    """
    workspace_context = _load_workspace_context(workspace_id)
    user = EMERGENCY_BRIEF_USER_TEMPLATE.format(
        workspace_id=workspace_id,
        workspace_context=workspace_context,
        source=ticket_input.source,
        ticket_id=ticket_input.ticket_id or "(none)",
        title=ticket_input.title,
        reporter=ticket_input.reporter or "(unknown)",
        received_at=ticket_input.received_at or "(unknown)",
        linked_ticket_ids=", ".join(ticket_input.linked_ticket_ids) or "(none)",
        body=ticket_input.body,
    )

    payload = call_json(system=EMERGENCY_BRIEF_SYSTEM, user=user)

    # Stamp server-authoritative fields. The LLM doesn't get to set
    # workspace_id or generated_at — those are caller / clock.
    payload["workspace_id"] = workspace_id
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()

    # Defend the no-auto-action invariant against an LLM that returns
    # `requires_pm_approval: false`. Force True on the way out.
    proposed = payload.setdefault("proposed_solution", {})
    if not isinstance(proposed, dict):
        proposed = {}
        payload["proposed_solution"] = proposed
    proposed["requires_pm_approval"] = True

    return EmergencyBrief.model_validate(payload)
