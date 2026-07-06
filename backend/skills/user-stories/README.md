# ticket

Turn a `prd-author` artifact (Part A human PRD + Part B Implementation Spec)
into editable, tracker-ready tickets in the Sprntly UI, and push them
accurately to ClickUp, Jira, Asana, Monday.com, Linear — or any tool via the
generic adapter.

**Replaces:** `user-stories` and `story-mapping` (flat tickets + story map,
one skill, auto-sizing heuristic).
**Upstream:** built on `prd-author` v4.2 — this skill packages the spec; it
never re-derives or rewrites what the spec states.

## When to call it

Call `ticket` when the *what* is decided and a PRD exists:
- "create tickets" · "break this into stories/tickets" · "story breakdown"
- "turn this PRD into Jira / ClickUp / Asana / Monday" · "push to <tool>"
- a ticket's comments need triage, or a proposed change needs routing

Do **NOT** call it to decide what to build (`prd-author`), grade the PRD
(`prd-critique`), or write deep technical design (`tech-spec`).

## Inputs → Outputs

**In (required):** the prd-author artifact — ideally both parts. Part A § 5 is
the requirements table (`ID | Requirement | Priority | Signal/Source |
Acceptance`); Part B carries EARS, contracts, the stakes gate, tasks, spec-
first tests, [ESCALATE], [ASSUMPTION → T0], Done-when.
**In (optional):** team roster (expertise matching) · connected tracker ·
existing backlog (dedupe) · design-agent handle.
**Out:** the editable ticket set (list + detail views) · decision tickets
routed to owners · a spike per [ASSUMPTION → T0] · approved-change propagation
· a backend field-mapped sync to the chosen destination.

## The inheritance contract (what maps to what)

| prd-author produces | becomes |
|---|---|
| Part A § 5 rows | tickets — priority from the table, provenance `PRD § 5 R#` |
| Part B spec-first tests | acceptance criteria, **verbatim**; failure branches → `[failure]` items; the card's `N AC` chip equals the inherited test count |
| Part B tasks + `[P]` | child issues, dependency order, parallel flags |
| Stakes gate | route: `agent-ready → Claude Code` vs `needs-human` |
| `[ESCALATE]` | decision tickets (owner · decide-by · blocked tickets) |
| `[ASSUMPTION → T0]` | a timeboxed spike whose result writes back to Part B |
| `[NEED]` markers | preserved on tickets — never filled with invented numbers |
| Done-when | the epic-level completion check |

**Degradation:** Part A only → generate INVEST stories flagged
`GENERATED ⚠ not inherited`, no routing, upgrade note to `prd-author`. No PRD →
recommend `prd-author` first.

## How to implement (for the building agent)

1. **Open `examples/sprntly-ticket-views.html`** — the locked design
   reference. Colors, type, spacing are fixed; the skill never restyles.
2. **Scope boundary:** render from the "Tickets from …" header block down.
   The page tab bar (Evidence / PRD / Tickets, Save/Share) is page chrome —
   never render it.
3. **List view:** header (serif ~24px title with the PRD name in italic
   green, subline, ⟳ Regenerate + ✓ Push to <Tool>), green ✳ intro, ticket
   cards (`T-#` chip · bold title · 2-line story preview ending in
   provenance · URGENT/HIGH/NORMAL pill · `N AC` chip), then a Decision-
   tickets group.
4. **Detail view:** header strip → edit-hint line → **full-width Description**
   (five labeled sections: What / Why now / User story / Scope bullets / Out
   of scope, grounding footer with `[NEED]`s named) → a two-column zone: main
   (Acceptance criteria → Child issues → Linked issues → Attachments with
   deep links opening in a new tab → Activity with one AI summary and
   Accept & propagate) beside the **Details rail** (Status + stacked fields).
   Everything is editable in place except inherited AC (comment loop only).
   Output artifacts carry no meta/contract footers.
5. **Push flow:** ✓ Push opens the destination picker (compact: projects with
   space paths, create-new, "remember for this PRD"); selection persists per
   PRD/workspace; the field-mapped sync then runs **on the backend** —
   mapping is logic (`references/field-mapping.md`), never UI. Sprint
   detection runs by default (reuse the live sprint, else create).
6. **Change loop:** comments are summarized; a proposed spec change renders
   an Accept & propagate card — on accept, update the ticket's criteria, the
   PRD row + Part B test (version bump), and the design artifact, then
   re-check traceability. Never apply silently.

## Story mapping (inside this skill)

When the gate says LARGE, the map is rendered as a **"Story map" tab beside
"Tickets"** — same output, same skill. Method (Jeff Patton, absorbed from the
retired `story-mapping`): gray backbone cards from Part A §4 activities in
narrative order → the same tickets placed under each activity → the
**walking skeleton as Release 1** (green band, crosses the whole journey) →
release slices aligned to §8 rollout phases (each slice end-to-end coherent;
slices map to sprints on push) → gaps/edges as dashed notes feeding back to
the PRD, never silent tickets. The map organizes the ticket set; it never
invents tickets.

## Visual references (agents: always open these first)

| File | Shows |
|---|---|
| `examples/sprntly-ticket-views.html` | Locked design reference — list + full detail anatomy |
| `examples/guided-setup-prd.md` → `examples/guided-setup-tickets.html` | End-to-end worked example, flat branch (gate: tickets only) |
| `examples/story-map-sample.html` | Large branch — Tickets + Story map tabs, functional switching |

If a render disagrees with these files, the files win.

## Files

- `SKILL.md` — the full spec (consumption contract, moving parts, delivery
  format, quality bar)
- `examples/sprntly-ticket-views.html` — locked design reference
- `examples/guided-setup-prd.md` + `examples/guided-setup-tickets.html` —
  end-to-end worked example: a real prd-author artifact and the ticket set
  this skill generated from it

## Version

v4 — 2026-07-03. Renamed `delivery-tickets` → `ticket`; story-map method integrated with the MUST gate and gray backbone; visual-reference contract added.
Previously v3 — Screenshot-locked UI (page-chrome boundary, ~24px header),
Jira-anatomy detail with full-width content + horizontal Details bar,
five-section description contract, single AI-summary comment pattern,
push-time destination picker, backend-only field mapping, end-to-end worked
example included.
