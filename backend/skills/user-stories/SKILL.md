---
name: user-stories
description: Turn a PRD's requirements (whether a table or written as prose) plus its Implementation Spec into editable, tracker-ready tickets in one call — auto-deciding whether the feature is large enough to need a story map first. Reads ticket comments and posts an AI summary; when a comment proposes a change to the spec, it summarizes the proposed update inside the comment thread for human approval, and on approval propagates the change to the story, the PRD, and the design agent as applicable. Syncs tickets bidirectionally to Jira, Linear, Asana, and Monday.com — and auto-adapts to whatever tool the team actually uses by discovering its real field configuration, plus a generic adapter that can introspect and map to ANY project-management tool. Use when the user says "create tickets", "break this into stories/tickets", "turn this PRD into Jira", "story breakdown", "send to Linear/Asana/Monday". Supersedes the separate user-stories and story-mapping skills — one skill does both, and is smart enough to skip the story map for small features. Never invents data; acceptance criteria are inherited from the spec, not rewritten.

---

# Delivery Tickets — requirements → tickets (+ story map when needed) → sync

## What it does
One skill that takes you from a decided PRD to delivery, end to end, so you never call ticketing and mapping separately:
1. **Reads the PRD requirements** — whether they're a table (`# + **Label:** detail` with inline `[edge case]`/`[failure]` tags) or written as prose — as the baseline unit of work, and **inherits the Implementation Spec** (EARS, tasks, acceptance tests, `[ESCALATE]`, autonomy gate) when present.
2. **Decides structure automatically.** Small/single-activity feature → a flat list of tickets, no story map. Large/multi-activity feature → it **builds a story map first** (backbone of user activities → release slices), then the tickets sit under it. The user doesn't choose; the skill sizes it (see Sizing).
3. **Creates editable tickets** — each scoped, prioritized, sized, category-tagged, and assigned by expertise, carrying provenance (`From PRD § …`), inherited acceptance criteria, traceability, and an `agent-ready`/`needs-human` route. Every ticket is editable before it's sent.
4. **Comment intelligence.** Reads each ticket's comments and posts an **AI summary** (alignment + open threads). When a comment **proposes a change to the spec**, it drafts the proposed update, posts it **in the comment thread for human approval**, and on approval **propagates** the change to the story/ticket, to the **PRD** (if the change affects requirements), and to the **design agent** (if it affects the prototype) — keeping all artifacts in sync.
5. **Delivers & syncs.** Renders the Sprntly delivery views (ticket list + ticket detail) and **syncs bidirectionally to Jira, Linear, Asana, and Monday.com**, mapping the canonical ticket to each tool's real fields (`references/field-mapping.md`).

It **never invents**: numbers, criteria, and assignees come from the PRD/spec; acceptance criteria are **inherited from the spec's tests, not rewritten** (so tickets can't drift from what the agent builds against).

## When to use / when NOT to use
- **Use** once the *what* is decided (PRD ± spec) and you need the delivery breakdown, a story map for a big feature, comment triage, or a push to a tracker.
- **Do NOT use** to decide what to build (`prd-author`), author the spec (`prd-author` Part B / `implementation-spec`), or re-litigate scope (`prd-critique`).

## Inputs
- **Required:** the PRD — its **requirements** are the baseline (table *or* prose; the skill parses both, and reads inline `[edge case]`/`[failure]` tags as required branches).
- **Strongly recommended:** the **Implementation Spec** — when present, acceptance criteria, dependency-ordered tasks, `[ESCALATE]` items, and the autonomy gate are **inherited**, not regenerated.
- **Optional:** existing ticket **comments** (for the summary + change loop); personas; the team's point scale; the target tracker + its project/board/field config; the design-agent handle (for prototype propagation).
- **Hard rule:** provided inputs only. No spec → generate INVEST criteria from the prose and flag it as a weaker guarantee. No persona → generic, flagged. Never fabricate a figure, owner, or criterion.

## Method
### 1. Parse requirements (table or prose) into work units
Each requirement (a table row, or a sentence/bullet in prose) becomes a candidate unit. Carry its label, its detail, and any inline `[edge case]`/`[failure]` tag. If a spec exists, align each requirement to its EARS id (R#/E#) and the spec task(s) that satisfy it.

### 2. Inherit the spec (if present)
Take EARS requirements, dependency-ordered tasks (`[P]` markers), acceptance tests (Given/When/Then), the `[ESCALATE]` list, and the stakes/autonomy gate. **Tag inheritance:** every `[edge case]`/`[failure]` requirement must yield that branch as a ticket acceptance scenario.

### 3. Decide structure (sizing — the skill is smart about this)
- **Flat tickets (no story map)** when the feature is one user activity / a handful of requirements / one release. *Most features.*
- **Story map first** when it spans **multiple user activities**, **>~12 requirements**, or **more than one release/sprint**, or the PRD/spec describes phased rollout. Then: build the backbone (left-to-right user activities), place stories beneath each, slice horizontally into releases (walking skeleton = release 1), and generate tickets per slice.
- State which path was taken and why in one line.

### 4. Create editable tickets
Each ticket carries: title · category (Product/Analytics/Reliability/CS/Localization/…) · `From PRD § …` provenance · priority (P0–P3) · points · labels · assignee **by expertise** · dependencies · route (`agent-ready`/`needs-human`) · **human block** (story + context) · **machine block** (inherited Given/When/Then + traceability `task → R# → test → PRD goal`). **Every field is editable before send**; clicking a ticket opens the full editable view.

### 5. Escalations → decision tickets
Each `[ESCALATE]` becomes a decision ticket: the decision, the owner, the tickets it blocks, route `needs-human`.

### 6. Comment intelligence + change-propagation loop
- **Summarize:** read all comments on a ticket; post a short **AI summary** — what's aligned, which threads are open, who owns each.
- **Detect change requests:** if a comment proposes altering behavior/acceptance/scope (e.g. "collapse step 3 with Show all", "skip should be skippable from any step"), draft the **proposed update** in spec terms.
- **Approval in-thread:** post the proposed update **as a comment reply for a human to approve** ("Proposed update: … — Approve / Edit / Reject"). Do **not** apply it silently.
- **Propagate on approval:** once approved, update (a) the **ticket/story** acceptance criteria, (b) the **PRD** if the change touches a requirement (note the version bump), and (c) signal the **design agent** if it touches the prototype. Re-run the traceability check so the chain stays intact. Record what changed and where.

### 7. Adapt to the team's tool & available fields (automatic, on the backend)
Field discovery and mapping run **on the backend, by default** — the user never hand-maps fields. The skill resolves the team's real configuration and writes through automatically:
- **Detect & introspect** the connected tool(s): work types, statuses, priority scheme, **custom fields**, columns, members, sprints/cycles.
- **Auto-map canonical → existing fields, server-side.** Set only fields that exist (no Story-Points field → skip it; Linear uses cycles → set cycle). No user step.
- **Degrade gracefully + flag.** A field the tool can't hold folds into the closest container, flagged — never dropped or invented.
- **Only genuinely low-confidence matches surface** for a one-tap confirm; everything else resolves automatically.
- **Sprint detection runs by default.** Inspect the team's existing sprints/cycles: **if this project is already in flight in an existing sprint, sync to that sprint**; otherwise **create a new sprint/cycle and sync to it.** The safe default is a *new* sprint — only reuse when an active sprint already covers this project. On tools without native sprints (Asana/Monday), use the closest container and flag it.

### 8. Deliver & sync
- Render the two delivery views (list + detail — see Delivery format).
- On "send to Jira/Linear/Asana/Monday" (or "send all"), **map canonical → tool fields** per `references/field-mapping.md`, create/update items **idempotently** (store the returned tool id; never duplicate on re-sync), and **pull back** status/assignee/comments for reconciliation.

## Delivery format (how it looks — matches the Sprntly UI)
- **List view ("Tickets from PRD v0.3"):** an opening line ("I've broken PRD v0.3 into N implementable tasks — scoped, prioritized, and assigned by expertise. Review or edit any before sending to Jira or Claude Code."), then a card per ticket: key · category icon · title · `From PRD § …` source line · priority pill · points · tags · assignee avatar. Footer: "Click a chunk to edit it as a full ticket. Or say 'send all to Jira'."
- **Detail view (one ticket):** key + back · priority/status/sprint selectors · Person responsible (+ reassign) · Description with acceptance criteria · Attachments (PRD §, prototype, evidence) · Comments with an AI **Summary** block on top, threaded comments, and an "Ask about this ticket" input. Every field editable.

**Editable fields (everything the user can change inline):**
- **Title / name** — rename the ticket.
- **Description + acceptance criteria** — fully editable rich text.
- **Attachments** — add (file or pasted link) or remove any attached PRD / prototype / evidence / file.
- **Person responsible** — reassign, or **add a person from the team** via a member picker (read from the connected workspace's members).
- **Priority · status · sprint** — selectors; the **sprint selector reads the team's existing sprints/cycles or creates a new one and syncs** (see step 7).
- **Priority/points/labels/category** — edit before send.
Edits are local until sync; on sync they map to the tool's fields (or degrade + flag) per `references/field-mapping.md`.

## Design reference (for the building agent)
The canonical look-and-feel lives in **`examples/sprntly-delivery-views.html`** — four rendered views: (1) the ticket **list**, (2) the editable ticket **detail** (with the rename pencil, editable description, add/remove attachments, the team **add-person** picker, the **sprint read-or-create** dropdown, and the in-thread **proposed-change approval** card), (3) the **sync / field-mapping** view showing auto-adaptation to the team's tool + the generic adapter, and (4) the **story map** (Jeff Patton backbone → stories → release slices, walking skeleton highlighted) — **view 4 renders only when the sizing heuristic triggers story mapping**; small features skip it and show only views 1–3. An agent implementing this skill should open that file and **align its build to it** — the views are the source of truth for layout, affordances, and states.

**Fixed design tokens (locked — never restyle).** These are pinned; the skill never changes them:
- Paper `#f6f5f1` · Card `#ffffff` · Line `#e9e8e4`
- Ink `#15171c` · Ink-2 `#41444f` · Soft `#80838d`
- Green (primary/accent) `#1a8a52` · Green-dark `#157045` · Green-soft `#e6f3ec`
- Priority P0 `#c0473c` on `#fbece9` · P1 `#b07a2e` on `#f6efe0` · P2 `#3f63a0` on `#e9eef7`
- Serif (titles) Spectral · Sans (body/UI) Inter
Only the ticket content varies. Backgrounds, accents, pills, and type are constant across every run.

## Accuracy & enforcement (carried — non-negotiable)
- **No fabrication:** numbers, owners, criteria from the PRD/spec only; unknowns labeled.
- **Inherit, don't rewrite:** acceptance criteria come verbatim from the spec's tests; `[edge case]`/`[failure]` tags become required scenarios.
- **Provenance required:** every ticket links back to its PRD section / spec task.
- **Approval before change:** a proposed spec change is shown in-thread and applied only on human approval; then propagated to story/PRD/design with the traceability re-checked.
- **Idempotent sync:** re-syncing updates, never duplicates.
- **Colors are fixed.** The skill never changes colors, background colors, or any visual token — the palette in the Design reference is **locked**; only content varies. Pin what shouldn't vary; generate only the content.

## Sizing heuristic (when to build a story map)
Story map if **≥2 of:** multiple distinct user activities; >~12 requirements; >1 release/sprint; phased rollout named in the PRD/spec; cross-team delivery. Otherwise flat tickets. Always state the call.

## Generic adapter (works with ANY project-management tool)
Beyond the four first-class tools, the skill includes a tool-agnostic adapter so it can target a tracker it has never seen:
1. **Introspect the schema.** Via the tool's API (or an exported field list), enumerate every field/column and its type (text, single-select, multi-select, user, number, date, relation/link, rich-text).
2. **Infer each field's semantic role** from its name + type: a single-select with values like P0/High/Low → **priority**; a number named points/estimate/SP → **points**; a user/people field → **assignee**; a workflow/status field → **status**; a long rich-text → **description**; a relation/link field → **parent/dependencies**; a date → **due date**. Produce a mapping with a confidence score.
3. **Confirm & remember.** Show the proposed mapping (canonical → discovered field, with confidence); let the user correct any row once; **persist the mapping per workspace** so it's automatic next time.
4. **Write through the discovered mapping**, honoring the tool's value formats (e.g. select-by-id vs text). Anything with no home is flagged, not dropped.
This is how the skill "auto-adjusts based on what the team uses and the fields they have available" — the four named tools are pre-mapped; everything else is handled by discovery + inference + a one-time confirm.

## Sync contract (Jira / Linear / Asana / Monday)
Map per `references/field-mapping.md`. Tool-specific musts: Jira description = ADF + Story Points is a per-site custom field (resolve by name) + set `parent` (Epic Link deprecated); Linear priority = int 0–4, estimate = points, team UUID required; Asana priority/points = custom fields set by option gid; Monday set status/priority by `index`, points → Numbers, deps → Connect Boards. Unsupported field → closest container + flag, never dropped.

## Quality checklist (the bar)
- [ ] Requirements parsed from **table OR prose**; inline tags become scenarios.
- [ ] Spec inherited when present (criteria not rewritten); generate-mode flagged when not.
- [ ] **Story-map decision made automatically** and stated; small features stay flat.
- [ ] Tickets editable; each carries category, provenance, priority, points, assignee-by-expertise, route, dependencies, human + machine blocks, traceability.
- [ ] Each `[ESCALATE]` → a decision ticket with owner + blocked tickets.
- [ ] Comments summarized; proposed changes shown **in-thread for approval**, then propagated to story/PRD/design with traceability re-checked.
- [ ] Sync maps to each tool's real fields, idempotently, with bidirectional reconciliation.
- [ ] Nothing fabricated; provenance on every ticket.

## Known gaps / limitations
- Inherited criteria are only as good as the spec (pair upstream with `prd-critique`).
- Auto-sizing for the story map is a heuristic; it states its call so a human can override.
- Cross-tool field parity isn't perfect (sprint/cycle have no Asana/Monday native equal) — mapped to the closest container and flagged.
- Change propagation updates artifacts it can reach; a PRD/design owned outside the system needs the approved-change note applied there too.

## Replaces
`user-stories` and `story-mapping` — this skill absorbs both (flat tickets + story map), adds comment intelligence, change propagation, and multi-tool sync. Keep one skill, not three.
