---
name: incident-runbook
description: Structure incident response and the post-incident review. Use when the user says "incident runbook", "we have an incident", "postmortem", "post-incident review", "sev1", or "what's our incident process". Produces a severity-tiered response runbook and a blameless postmortem template focused on systemic fixes, not blame.
---

# Incident Runbook

## What it does
Provides two linked artifacts: a severity-tiered incident-response runbook (who does what, when, how to communicate during an active incident) and a blameless post-incident review that drives to systemic root causes and owned corrective actions. It separates "stop the bleeding" from "stop it recurring."

## When to use / when NOT to use
- **Use** to prepare incident process, run an active incident, or review one afterward.
- **Do NOT use** for sprint retros (`retrospective`) or pre-launch risk (`pre-mortem`).

## Inputs
- **Required:** the context (preparing a process, or details of an active/past incident).
- **Optional:** severity definitions, on-call structure, comms channels, customer impact. *If missing, propose standard severity tiers and label them for the team to ratify.*

## Method (methodology)
Severity tiering + incident command + blameless postmortem (SRE practice) + corrective-action discipline.
1. **Severity tiers** - define Sev1-3 by customer/business impact, with response expectations per tier.
2. **Roles** - incident commander, comms lead, ops/eng - so the response isn't chaotic.
3. **Active-incident flow** - detect -> declare -> mitigate -> communicate (internal + customer) -> resolve -> stand down.
4. **Communication templates** - status-page/customer updates at intervals, honest and non-technical.
5. **Blameless postmortem** - timeline, impact, root cause(s) via "5 whys" on the *system* not the person, what went well, what didn't.
6. **Corrective actions** - specific, owned, dated; distinguish quick fixes from systemic prevention; track to closure.

## Output spec
Severity-tier table · roles · active-incident flow · comms templates · a blameless postmortem template (timeline, impact, root cause, actions) · corrective-action tracker. Tone: systemic, never blame-seeking.

## Sprntly integration (optional)
- **Inputs from Sprntly:** monitored early-warning signals (from `pre-mortem`); related prior incidents from the knowledge graph.
- **Outputs to Sprntly:** corrective actions written to the backlog with owners; the incident + root cause stored as durable knowledge so recurrence is caught.
- **Degrades to:** standalone; propose standard tiers.

## Quality checklist (the bar)
- [ ] Severity is defined by impact, with response expectations per tier.
- [ ] Roles are assigned (no leaderless scramble).
- [ ] The postmortem is blameless and reaches systemic root cause.
- [ ] Corrective actions are specific, owned, dated, and tracked to closure.

## Known gaps / limitations
- A runbook doesn't create on-call readiness; it must be drilled.
- Blamelessness is cultural; the template encourages it but leadership behavior decides whether it holds.
- This is product/ops incident process, not a security IR plan - security incidents need dedicated protocols.

## Worked example
**Input:** "Export feature went down for 3 hours."
**Output (abridged):** Severity: Sev2 (feature-down, no data loss, workaround exists). Roles: IC = on-call lead, comms = PM. Flow: detected via error-rate alert -> declared -> rolled back deploy -> status-page update every 30 min -> resolved -> stand down. Postmortem root cause (5 whys on system): deploy lacked export integration test -> CI didn't cover the path. Actions: add export e2e test (owner: eng, this week); add export to the smoke suite (owner: eng, next sprint). Lesson stored.
