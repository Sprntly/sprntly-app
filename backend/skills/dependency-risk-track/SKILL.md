---
name: dependency-risk-track
description: Map cross-team dependencies and track delivery risk. Use when the user says "track dependencies", "what's blocking us", "dependency map", "delivery risk", "critical path", or "we're waiting on another team". Produces a dependency map with owners and dates, the critical path, and a risk register with mitigations for the dependencies most likely to slip.
---

# Dependency & Risk Tracking

## What it does
Makes delivery risk visible: it maps what the initiative depends on (other teams, vendors, decisions, infra), identifies the critical path, and maintains a risk register that flags the dependencies most likely to slip - with owners, needed-by dates, and mitigations - so blockers are managed before they become missed dates.

## When to use / when NOT to use
- **Use** for multi-team/complex delivery where things outside the team's control can block it.
- **Do NOT use** for pre-launch failure brainstorming (`pre-mortem`) or status communication (`status-report`).

## Inputs
- **Required:** the initiative and its known dependencies/blockers.
- **Optional:** owners, dates, team commitments, the delivery deadline. *If missing, map the structural dependencies and flag the unknown owners/dates as the first risk.*

## Method (methodology)
Dependency mapping + critical-path identification + risk register (likelihood x impact) + escalation triggers.
1. **Enumerate dependencies** - by type: other teams, vendors/APIs, decisions/approvals, infra, data, design.
2. **For each:** owner, what's needed, needed-by date, current status.
3. **Critical path** - the chain of dependencies that determines the earliest finish; a slip here slips the whole thing.
4. **Risk register** - rate each dependency by likelihood-of-slip x impact; the high-high ones are the watch list.
5. **Mitigations** - for top risks: parallel path, earlier ask, fallback, or de-scope; assign an owner.
6. **Escalation triggers** - the date/condition at which a slipping dependency must be escalated, and to whom.

## Output spec
Dependency map (type · owner · needed-by · status) · the critical path · risk register (likelihood x impact, ranked) · mitigations + owners for top risks · escalation triggers.

## Sprntly integration (optional)
- **Inputs from Sprntly:** in-flight items + their dependencies from the backlog; cross-team signals; prior slip patterns from the outcome graph.
- **Outputs to Sprntly:** dependencies + risks tracked as monitored items; escalation triggers become alerts; mitigations written to the backlog.
- **Degrades to:** standalone from the dependency list.

## Quality checklist (the bar)
- [ ] Dependencies span teams/vendors/decisions/infra, not just eng tasks.
- [ ] Every dependency has an owner and a needed-by date (or it's flagged as risk #1).
- [ ] The critical path is identified.
- [ ] Top risks have an owned mitigation and an escalation trigger.

## Known gaps / limitations
- It surfaces and tracks risk; it can't make another team deliver - escalation triggers are the lever.
- Critical-path accuracy depends on honest date estimates from dependency owners.

## Worked example
**Input:** "Collab launch depends on: infra team's websocket capacity, a security review, and a design decision on conflict UX."
**Output (abridged):** Deps: websocket capacity (owner infra, needed-by 6/10, status: not started - RISK), security review (owner sec, 6/15, queued), conflict UX decision (owner design, 6/03, in progress). Critical path: infra capacity -> load test -> GA. Top risk: infra not started (high x high). Mitigation: PM escalates to infra lead now for a committed date; fallback = cap beta to 50 accounts. Escalation trigger: if no infra start by 6/05, escalate to eng director.
