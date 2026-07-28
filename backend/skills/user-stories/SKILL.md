---
name: user-stories
description: Turn a prd-author artifact (Part A human PRD + Part B Implementation Spec) into editable, tracker-ready tickets that push accurately to ClickUp, Jira, Asana, Monday.com, Linear, or any other tool. Built ON TOP of prd-author v4.2's output contract — it consumes the Part A §5 requirements table (ID | Requirement | Priority | Signal/Source | Acceptance) and inherits Part B verbatim: acceptance criteria from spec-first tests (success + failure branches), routing from the stakes gate, decision tickets from [ESCALATE], a spike from [ASSUMPTION → T0], dependency order and [P] markers from tasks, epic completion from Done-when. Auto-decides whether a story map is needed. Includes comment intelligence with approve-and-propagate change flow, backend field auto-mapping, and default sprint detection. Use when the user says "ticket this", "make tickets", "create tickets", "break this into stories/tickets", "turn this PRD into Jira/ClickUp/Asana/Monday", "push to <tool>", or "story breakdown". This one skill absorbs both flat tickets and the story map, superseding the separate story-mapping skill.
---

# Ticket (spec-consuming, tracker-accurate)

Turn the `prd-author` artifact into delivery: editable tickets in the Sprntly
UI that sync 1:1 to whatever tracker the team runs. One skill covers flat
tickets and story maps, decides which is needed, and keeps every downstream
artifact traceable back to the PRD.

**Replaces** `user-stories` and `story-mapping` — flat tickets + the story map
are absorbed here; keep one skill, not three.

## Built on prd-author (the upstream contract)

This skill is downstream of `prd-author` v4.2 and consumes its output shape
exactly. The mapping is fixed:

| prd-author produces | ticket consumes it as |
|---|---|
| Part A §5 requirements table (`ID \| Requirement \| Priority \| Signal/Source \| Acceptance`) | One or more tickets per requirement row; the ticket's priority comes from the table's Priority column; provenance is `Part A §5 R#` |
| Part A §4 users & scenarios | Story voice (`As a <user in scenario>…`) — personas are never invented |
| Part B · EARS requirements (traced to Part A IDs) | The trace spine: `ticket → Part A §5 R# → Part B EARS E# → spec-first tests → PRD goal` on every build ticket |
| Part B · Spec-first tests (success + failure branches) | **Acceptance criteria, inherited verbatim** — never rewritten. Failure branches render as `[failure]`-tagged criteria. AC count is shown on the ticket card (`5 AC`) |
| Part B · Tasks (dependency-ordered, `[P]`) | Ticket dependency order; `[P]` marks parallel-safe tickets; agent-shaped tasks are regrouped into vertically-sliced user-valued tickets and attached as subtasks |
| Part B · Release plan (B7) | **Story-map release slices, inherited verbatim** — each ticket's `release` label comes from the phase shipping its task; only a Part B without one (or no Part B) triggers phase synthesis |
| Part B · Stakes gate | Routing: `agent-ready → Claude Code` (reversible, fully specified) vs `needs-human` (stakes-gated or escalation-blocked) |
| Part B · `[ESCALATE]` items | **Decision tickets** — the decision, an owner, decide-by, and the build tickets each blocks |
| Part B · `[ASSUMPTION → T0]` contracts | A **spike ticket (T-0)**, timeboxed, whose exit condition is the contract's validation; result writes back to Part B |
| Part B · Cross-cutting checklist | Unchecked items become subtasks or `[failure]` criteria on the tickets they belong to — never dropped |
| Part B · Done-when | The **epic-level completion check**: the ticket set is done when all inherited spec-first tests pass and every `[ESCALATE]` is resolved. Never restated per-ticket in conflicting words |
| Part A `[NEED: …]` markers | Surface as data-gap notes on affected tickets; numbers are never invented to fill them |

**Degradation, in order:** Part A only (no Part B) → generate INVEST stories
with Given/When/Then from the §5 table's Acceptance column, every criteria
block flagged `GENERATED ⚠ not inherited`, no routing (no stakes gate exists),
and an upgrade note pointing at `prd-author` for a Part B. No PRD at all →
recommend running `prd-author` first; only on explicit insistence generate
from prose with the same flags.

## When to invoke

"create tickets" · "break this into stories/tickets" · "turn this PRD into
Jira/ClickUp/Asana/Monday" · "push to <tool>" · "story breakdown" · a ticket's
comments need triage or a proposed change needs routing. NOT for deciding what
to build (`prd-author`), grading the PRD (`prd-critique`), or deep technical
design (`tech-spec`).

## The moving parts

1. **Requirements parser** — reads Part A §5 whether rendered as a table or
   prose; aligns each row to its Part B EARS id and tasks when Part B exists;
   inline `[edge case]`/`[failure]` tags become required scenarios.
2. **Spec inheritance** — criteria verbatim from Part B spec-first tests; the
   full trace chain rides on every ticket; "propose changes in comments — don't
   edit here" is enforced on inherited blocks.
3. **Auto story-map decision (the sizing gate)** — the map is additive, never
   the default. Score the PRD against five signals: {multiple user activities
   in Part A §4, more than ~12 requirements in §5, requirements that only
   deliver value in more than one coherent slice when sequenced, a Part B
   release plan with more than one phase, cross-team delivery}. **≥2 signals → the skill MUST
   build the story map, integrated in the same output as a "Story map" tab
   beside "Tickets" — one skill, one output; otherwise tickets only.**
   The intro line always states the call and why (e.g. "Story map: not needed
   — sized flat (1 activity, 8 requirements, single release)" or "Story map:
   built — 3 activities across 2 releases").

   **Rollout phases are SYNTHESIZED here, not read from the PRD** (prd-author
   v4.4 retired the Rollout section; the human PRD deliberately carries no
   release plan). When the map is warranted, derive ordered phases from the
   §5 requirements and Part B's dependency order — Release 1 is always the
   walking skeleton — and label each ticket's `release` with its phase
   ("Release 1 — walking skeleton", "Release 2 — <short scope name>", …).
   Phase labels name scope only: never invent dates, audiences, or exit
   criteria. When Part B ships a release plan, inherit its phases verbatim
   instead of synthesizing. The phases are a PROPOSAL — they surface as the
   map's release slices, editable like any ticket field before push.

   **When the map is built (method absorbed from the retired
   `story-mapping`):** Jeff Patton mapping over the same tickets — the map
   organizes the ticket set; it never invents extra ones.
   1. *Backbone* — the user activities/steps from Part A §4, left to right in
      narrative order (the user's journey, never a feature list). Backbone
      cards render **light gray** (panel background, hairline border, ink
      text) — never black or heavy dark blocks.
   2. *Tickets under each step* — each generated ticket placed beneath the
      activity it serves, most essential at top.
   3. *Walking skeleton* — the top row: the minimal end-to-end path; this is
      release 1 and matches the tickets already marked walking-skeleton.
   4. *Release slices* — horizontal lines grouping tickets into releases,
      aligned to the synthesized rollout phases (or Part B's release plan
      when present); every slice leaves the user able to complete the
      journey (thinner, then richer).
   5. *Gaps & alternatives* — missing steps and error paths surface as
      `[NEED]`/`[edge]` notes on the map, feeding back to the PRD, never
      silently added as tickets.
   On push, release slices map to the tool's sprints/milestones; each map
   card deep-links to its ticket. Map quality bar: backbone = narrative
   journey; skeleton crosses the whole journey; each slice is end-to-end
   coherent; gaps noted.
4. **Editable tickets** — every field editable before sync (see Ticket
   contents).
5. **Comment intelligence + change loop** — posts an AI summary (aligned /
   open / owners); a comment proposing a behavior/criteria/scope change becomes
   a **Proposed update · awaiting your approval** card (Approve & propagate /
   Edit / Reject). On approval it propagates to the ticket's criteria, the PRD
   (Part A §5 row + Part B test, with a version bump), and the design agent if
   the prototype changes — then re-checks traceability. Never applied silently.
6. **Adaptive sync (backend, default-on)** — detects the connected tool, pulls
   its live schema (types, statuses, priorities, custom fields, members,
   sprints/cycles/lists), auto-maps canonical → existing fields server-side.
   **Sprint detection runs by default**: if the project is already in flight,
   reuse its sprint/cycle/list; otherwise create one. Fields the tool lacks
   degrade with a visible ⚑ flag — never silently dropped. Sync is idempotent
   (stores the tool id; re-sync updates, never duplicates) and bidirectional
   (status, assignee, comments pull back).
7. **Generic adapter** — for any unmapped tool: introspect fields → infer each
   field's role by name+type with confidence (P0/High select → priority; number
   "SP" → points; people → assignee; workflow → status; rich-text →
   description; relation → parent/deps; date → due) → confirm once → persist
   per workspace → write through honoring value formats.

## Ticket contents (the canonical ticket)

Every build ticket carries the full standard field set so it maps 1:1 to any
tracker: **key · type · title · description** (story + context + why-now,
citing the signal) · **priority** (from Part A §5) · **status · sprint/list ·
story points · time estimate · due date · epic/parent · labels · assignee**
(matched by expertise, reassignable) · **watchers · reporter ·
created/updated** · **acceptance criteria** (inherited, checklist-rendered,
count surfaced as `N AC`) · **subtasks** (from Part B tasks) · **dependencies**
(blocked-by / blocks, `[P]`) · **route** (agent-ready → Claude Code /
needs-human, from the stakes gate) · **attachments · comments · activity log**.

**Attachments are deep links** that open in a **secondary tab**, each to the
exact section — the PRD's §5 anchor for this requirement, the specific
prototype screen, the specific report finding (e.g. the VoC problem card) —
never the document root.

## Tracker mapping (accuracy is the contract)

Canonical → tool, with the real per-platform gotchas honored:

| Canonical | ClickUp | Jira | Asana | Monday.com |
|---|---|---|---|---|
| Title / Description | name · description (md) | summary · description (ADF) | name · html_notes | item name · long-text |
| Priority | priority 1–4 | priority | custom enum (set by option gid) | status column (set by index) |
| Status | list status | workflow status | section / custom enum | status column (index) |
| Assignee · Watchers | assignees · watchers | assignee · watchers | assignee · followers | people · subscribers |
| Points · Time est. | sprint points · time estimate | Story Points (per-site custom field) · timetracking | custom number field | numbers column |
| Sprint | sprint list | sprint | section-in-project ⚑ | group ⚑ |
| Epic / Parent | parent / list | `parent` (Epic Link deprecated) | parent task | subitem-of |
| Dependencies | waiting-on / blocking | issue links (blocks) | dependencies | dependency column + Connect Boards |
| Acceptance criteria | checklist items | description block (Gherkin) | subtask checklist | checklist in update |
| Labels · Due date | tags · due_date | labels · duedate | tags · due_on | tags · date column |

Linear: priority int 0–4, `estimate`, cycles. Everything else → the generic
adapter. ⚑ = nearest-container mapping, flagged to the user.

## Visual references (ALWAYS consult before rendering)

An implementing agent must open these before producing any output — they are
the source of truth for look, spacing, states, and both branches of the
sizing gate:

- `examples/sprntly-ticket-views.html` — the locked design reference: list
  view + full ticket detail (five-section description, AC checklist, details
  rail, picker, activity).
- `examples/guided-setup-tickets.html` — a real end-to-end output (flat
  branch: gate says tickets only) generated from
  `examples/guided-setup-prd.md`.
- `examples/story-map-sample.html` — the large branch: Tickets + Story map
  tabs with working switching; gray backbone, green walking-skeleton band,
  release slices, gap notes.

If a render disagrees with these files, the files win.

## Delivery format (locked to the Sprntly UI — colors are fixed, the skill never restyles)

Design reference: `examples/sprntly-ticket-views.html` — the source of truth
for layout, tokens, affordances, and states. An implementing agent must open
it and align. Palette tokens (locked): ink `#1c1e21`, panel `#f7f7f5`, green
`#2e8a57` / title-green `#2f7d52` / tint `#e9f4ec`, urgent `#c63f35`/`#fcebe8`,
high `#b57a21`/`#fbf1de`, chip `#f1f1ef`; Spectral (serif titles) + Inter
(body) + IBM Plex Mono (keys).

- **Scope boundary:** the skill's output starts at the "Tickets from …" header
  block. The surrounding page chrome — the Evidence / PRD / Tickets tab bar,
  artifact title, Save / Share / close — belongs to the Sprntly page, not to
  this skill; the skill never renders it.
- **Push flow:** clicking **✓ Push to <Tool>** opens a **destination picker**
  listing the tool's projects/spaces/lists (with a create-new option and a
  "remember for this PRD" toggle); the user selects where the tickets land,
  the choice is persisted per PRD/workspace, and the field-mapped sync then
  runs on the backend. Title sizing is modest — header serif ~24px, detail
  title ~20px — matching the reference screenshots.
- **List view:** header block — serif "Tickets from *<PRD name in italic
  green>*", subline "N tickets · generated from the PRD · Part B detected (or
  not)", then an actions row beneath: **⟳ Regenerate** and **✓ Push to
  <Tool>** (green, names the connected tool) — nothing else in the header
  block. Green ✳ intro line stating the count and the story-map call.
  Then a card per ticket: `T-#` key chip · bold title · two-line story preview
  ending in its `Context: … Part A §5 R#` provenance · **URGENT/HIGH/NORMAL
  pill** + **`N AC` chip**.
- **Detail view — standard product-ticket anatomy (two columns):**
  Header strip: crumb · `T-#` key chip · serif title · per-ticket **Push to
  <Tool>** button.
  **Main column (the story):**
  1. **Description — fixed labeled sections, radically consistent across every
     ticket** (never a merged prose block):
     - **What** — one plain sentence naming the deliverable.
     - **Why now** — 1–2 crisp sentences of background: the driving facts
       (signal-linked) and the window/urgency.
     - **User story** — one clean `As a… I want… so that…` sentence.
     - **Scope bullets** ("The card/feature must cover") — 3–5 short bullets.
     - **Out of scope** — one line, naming the ticket that owns the excluded
       work where relevant.
     - Grounding footer: link to the PRD §5 row + signals; `[NEED]` baselines
       named — never filled. Deep documentation stays in the linked artifacts,
       not pasted into the ticket.
  2. **Acceptance criteria** — inherited checklist with `[edge]`/`[failure]`
     tags; count in the header matches the card's `N AC` chip.
  3. **Subtasks** · **Dependencies**.
  4. **Artifacts** — PRD, Evidence, Prototype links, each opening in a **new
     tab**, deep-linked to the exact section.
  5. **Comments** — ONE AI-summary block (decision-centric: what's aligned +
     the pending decision with **Accept & propagate / Edit / Reject**;
     accepting updates the ticket's criteria, the PRD row/test with a version
     bump, and the design artifact, then re-checks traceability) and the
     comment thread with an "Ask about this ticket" input.
  **Layout:** the **Description spans the full page width** directly under the
  header (an edit hint line sits between them). Below it, a two-column zone:
  the main column (Acceptance criteria · Child issues · Linked issues ·
  Attachments · Activity) with the **Details rail beside it** — Status button,
  then stacked label/value rows (Assignee · Reporter · Priority · Labels ·
  Parent · Sprint · Story points · Due date · Time tracking · Route ·
  Watchers, Created/Updated footer).
  **Editability:** the detail is editable in place — title, description
  sections, fields, and child issues are click-to-edit and sync on push. The
  one exception is inherited acceptance criteria: read-only, changed only via
  the comment → Accept & propagate loop.
  **No meta footers:** output artifacts never carry contract-demonstration or
  input-contract notes — that explanation lives in this SKILL.md and the
  README, not in what users see.
  **Main-column section names follow tracker conventions:** Description
  (story + context) · Acceptance criteria · **Child issues** (from Part B
  tasks) · **Linked issues** ("is blocked by" / "blocks") · **Attachments**
  (deep links, new tab) · **Activity** with All / Comments / History tabs and
  a comment input.
  **Tracker mapping is never rendered in the ticket views.** The canonical →
  tool field mapping (ClickUp, Jira, Asana, Monday.com, Linear, generic
  adapter) runs on the backend at push time — it exists as logic and in
  `references/field-mapping.md`, not as UI. The only visible surface is the
  Push to <Tool> button and, on failure or degradation, a flagged toast.
- **Story-map board** (only when sized in): activity backbone across the top,
  story cards beneath, release-slice rows, walking skeleton = release 1 —
  same locked palette.

## Accuracy & enforcement (non-negotiable)

- **No fabrication** — numbers, owners, criteria from the PRD/spec only;
  `[NEED]` gaps stay visible, never filled by guessing.
- **Inherit, don't rewrite** — inherited criteria are read-only in the ticket;
  changes go through the comment → approval → propagate loop.
- **Provenance required** — every ticket links to its Part A §5 row and Part B
  ids; the AC count chip must equal the inherited test count.
- **Approval before change** — proposed spec changes are applied only on human
  approval, then propagated (ticket · PRD with version bump · design agent)
  with traceability re-checked.
- **Accurate sync** — map only to fields that exist; degrade with a visible
  flag; idempotent; bidirectional.

## Quality checklist (the bar)

- [ ] Every build ticket traces `ticket → Part A §5 R# → Part B EARS E# →
      spec-first tests → PRD goal`.
- [ ] AC inherited verbatim; failure branches present as `[failure]` items;
      the card's `N AC` chip equals the inherited count.
- [ ] Every `[ESCALATE]` became a decision ticket with an owner and
      blocked-ticket links; every `[ASSUMPTION → T0]` became a spike.
- [ ] Routing complete via the stakes gate; dependency order and `[P]`
      preserved; walking skeleton marked when a map is built.
- [ ] Story-map call stated (built or not, and why).
- [ ] All standard fields populated or explicitly empty — the ticket maps 1:1
      to ClickUp/Jira/Asana/Monday/Linear; degraded fields flagged ⚑.
- [ ] Attachments deep-link to exact sections and open in a secondary tab.
- [ ] Locked palette untouched; layout matches the design reference.
- [ ] In generate mode: every criteria block flagged as generated; upgrade
      path to `prd-author` stated.

## Known gaps / limitations

- Inherited criteria are only as good as Part B (pair upstream with
  `prd-critique`).
- Story sizing is advisory; only the delivery team can truly estimate.
- Regrouping agent-shaped tasks into user-valued tickets is judgment; the Part
  B dependency graph is the safeguard.
- Change propagation reaches artifacts inside the system; an externally-owned
  PRD or design needs the approved-change note applied there manually.

## Sprntly integration

- **Inputs:** the `prd-author` artifact; personas + existing backlog from the
  knowledge graph (dedupe); team roster (expertise matching); connected
  tracker credentials; design-agent handle.
- **Outputs:** the editable ticket set; decision tickets routed to owners;
  approved-change propagation; the tracker sync. `agent-ready` tickets carry
  the `→ Claude Code` handoff; completion is verified against the inherited
  spec-first tests — not vibes.
