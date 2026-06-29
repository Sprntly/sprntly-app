# delivery-tickets — README (LLM front door)

`SKILL.md` is the authoritative spec. This README orients an LLM/agent fast: what the skill is, where it sits, every moving part, and how to build to it. It **supersedes `user-stories` and `story-mapping`** — one skill does both.

## What it is
The delivery step of the PM pipeline. It turns a decided **PRD (± Implementation Spec)** into **editable, tracker-ready tickets**, builds a **story map only when the feature is large enough**, runs **comment intelligence** with an in-thread change-approval loop, and **syncs to whatever tool the team uses** — adapting to that tool's real fields, with a **generic adapter** for any tool it hasn't seen.

## Where it sits (the chain)
`evidence-brief → prd-author → implementation-spec → delivery-tickets → (Jira / Linear / Asana / Monday / any)`
It does **not** decide *what* to build (that's `prd-author`) or author the spec (`prd-author` Part B / `implementation-spec`). It packages the *what* into delivery and keeps every artifact in sync. Skills don't auto-call each other — an orchestrator (Sprntly) sequences them.

## When to invoke
"create tickets", "break this into stories/tickets", "turn this PRD into Jira/Linear/Asana/Monday", "story breakdown", "send all to Jira". Also when a ticket's **comments** need triage or a proposed change needs to be routed.

## Inputs → Outputs
- **In:** PRD requirements (**table OR prose** — both parsed; inline `[edge case]`/`[failure]` tags become required scenarios); Implementation Spec (optional but strongly recommended — its EARS/tasks/tests/`[ESCALATE]`/autonomy-gate are **inherited, not rewritten**); existing comments; team's tool + field config; team members; design-agent handle.
- **Out:** an editable ticket set (+ a story map if sized in); decision tickets for `[ESCALATE]`; comment summaries + approved-change propagation; a tool sync (idempotent, bidirectional).

## The moving parts
1. **Requirements parser** — reads the PRD requirements whether a table (`# + **Label:** detail`) or prose; aligns each to a spec EARS id + task when a spec exists.
2. **Spec inheritance** — acceptance criteria come verbatim from the spec's Given/When/Then; tags become test branches; traceability `ticket → task → R# → test → PRD goal` rides on every ticket.
3. **Auto story-map decision** — sizing heuristic: story map if ≥2 of {multiple user activities, >~12 requirements, >1 release, phased rollout, cross-team}; else flat tickets. Always states the call.
4. **Editable tickets** — title/name, description + acceptance criteria, attachments (add/remove), person responsible (reassign or **add from team**), priority/status/sprint, points/labels/category — all editable before sync.
5. **Comment intelligence + change loop** — posts an AI summary; when a comment proposes a spec change it drafts the update, posts it **in-thread for human approval** (Approve & propagate / Edit / Reject), and on approval propagates to the **ticket**, the **PRD** (if a requirement changes, with a version bump), and the **design agent** (if the prototype changes) — re-checking traceability.
6. **Adaptive sync (backend, automatic)** — field discovery & mapping run **server-side by default**; the user never hand-maps. Detects the connected tool, pulls its **live schema** (types, statuses, priorities, custom fields, columns, members, sprints/cycles), maps canonical → **only fields that exist**, degrades the rest with a flag, and surfaces only genuinely low-confidence matches for a one-tap confirm. **Sprint detection runs by default:** reuse an existing sprint if the project is already in flight there, otherwise **create a new sprint and sync** (new is the safe default).
7. **Generic adapter** — for any tool not pre-mapped: introspect fields → infer each field's role by name+type with confidence (P0/High select → priority, number "SP" → points, people field → assignee, workflow → status, rich-text → description, relation → parent/deps, date → due) → auto-resolve on the backend → persist per workspace → write through honoring value formats.

## Editable fields (what a user can change inline)
Title · description + acceptance criteria · attachments (add/remove) · person responsible (reassign / add from team) · priority · status · **sprint (pick existing or create new + sync)** · points · labels · category. Local until sync; on sync mapped per `references/field-mapping.md`.

## Field mapping & sync
Canonical ticket → Jira / Linear / Asana / Monday in `references/field-mapping.md`, with the gotchas (Jira ADF + Story-Points custom field + parent-over-Epic-Link; Linear int priority + estimate; Asana custom-field priority/points by option gid; Monday status-by-index + Connect Boards). Sync is **idempotent** (stores the tool id; never duplicates) and **bidirectional** (pulls status/assignee/comments back; conflicts flagged).

## Design references (build to these)
**`examples/sprntly-delivery-views.html`** is the canonical UI — four views: ticket **list**, editable ticket **detail** (rename pencil · editable description · add/remove attachments · team add-person picker · sprint read-or-create dropdown · in-thread proposed-change approval), the **sync / field-mapping** view (auto-adapt + generic adapter), and the **story map** (backbone → stories → release slices). **The story-map view renders only when the sizing heuristic triggers it** — small features skip it. An implementing agent should open it and align layout, affordances, and states to it.

## Guardrails (non-negotiable)
- **No fabrication** — numbers, owners, criteria from the PRD/spec only; unknowns labeled.
- **Inherit, don't rewrite** acceptance criteria; `[edge case]`/`[failure]` → required scenarios.
- **Provenance on every ticket** (`From PRD § …` / spec task).
- **Approval before change** — proposed spec changes are shown in-thread and applied only on approval, then propagated with traceability re-checked.
- **Adapt, don't assume** — auto-map on the backend; set fields the tool has; degrade + flag the rest; never invent a field or silently drop one.
- **Sprint by default** — detect automatically; reuse an in-flight sprint for the project, else create a new one and sync (new = safe default).
- **Colors are fixed** — the locked palette in the Design reference never changes; only content varies.
- **Idempotent sync** — update, never duplicate.

## Files
- `SKILL.md` — authoritative spec & workflow.
- `references/field-mapping.md` — canonical → tool field map + generic adapter logic.
- `examples/sprntly-delivery-views.html` — canonical delivery UI (list · editable detail · sync).
