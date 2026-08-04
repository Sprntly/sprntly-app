# Roadmap extraction — expected signal shape

Every signal this skill emits still fills the caller's fixed extraction
schema (`kind`, `content`, `source_type`, `theme`, `relationship`,
`properties`, `confidence` — see `app.graph.extractor._EXTRACT_SCHEMA`).
This doc pins the VALUES this skill is expected to choose, so its output can
be checked structurally against a declared contract rather than only
against free-text prose.

| Field                          | Expected value                                          |
|---------------------------------|-----------------------------------------------------------|
| `kind`                          | always `finding` (never `deal_blocker` — see SKILL.md §2) |
| `source_type`                   | always `pm_manual` (re-pinned by the caller regardless)   |
| `relationship`                  | always `AFFECTS`                                          |
| `properties.initiative_status`  | always present: `committed` \| `planned` \| `exploring`   |
| `properties.target_period`      | present only when the document states timing              |
| `properties.commercial_risk`    | present (`true`) only when the document names a commercial/revenue stake |

## Worked example — committed, inferred purely from hedge language

Input (chunk excerpt, no explicit status field anywhere):
```
Self-serve onboarding — basically done, just finishing up QA this week.
Unblocks the free-trial funnel.
```
Output:
```json
{
  "kind": "finding",
  "content": "Self-serve onboarding is basically done, finishing QA this week, unblocking the free-trial funnel",
  "source_type": "pm_manual",
  "theme": "Self-serve onboarding",
  "relationship": "AFFECTS",
  "properties": {"initiative_status": "committed"},
  "confidence": 0.85
}
```
No `target_period` — the source states none, so none is invented.

## Worked example — planned, with a stated period

Input:
```
Q3: ship AI-assisted PRD authoring for the design team.
```
Output:
```json
{
  "kind": "finding",
  "content": "AI-assisted PRD authoring for the design team is planned to ship in Q3",
  "source_type": "pm_manual",
  "theme": "AI authoring",
  "relationship": "AFFECTS",
  "properties": {"initiative_status": "planned", "target_period": "Q3"},
  "confidence": 0.85
}
```

## Worked example — exploring, openly uncertain

Input:
```
We're evaluating whether to build native SSO group sync or lean on our
IdP partner's API — no decision yet, revisiting next quarter.
```
Output:
```json
{
  "kind": "finding",
  "content": "Evaluating whether to build native SSO group sync or lean on the IdP partner's API, revisiting next quarter",
  "source_type": "pm_manual",
  "theme": "SSO",
  "relationship": "AFFECTS",
  "properties": {"initiative_status": "exploring", "target_period": "next quarter"},
  "confidence": 0.7
}
```

## Worked example — commercial blocker, still `kind: "finding"` (the collapse decision)

The description names a specific at-risk renewal, but `kind` stays
`"finding"` — never `"deal_blocker"` (that value belongs to
`hubspot-extraction`'s vocabulary, earned from a real CRM deal `stage`, not
this skill's). The commercial stakes ride into `properties` on the SAME
signal instead of producing a differently-classified one.

Input:
```
SSO group sync — blocked on Legal's review of the IdP contract. This is
holding up Acme Corp's renewal ($180k ARR), targeting end of Q3.
```
Output — `kind` stays `"finding"`:
```json
{
  "kind": "finding",
  "content": "SSO group sync is blocked on Legal's review of the IdP contract, holding up Acme Corp's renewal ($180k ARR), targeting end of Q3",
  "source_type": "pm_manual",
  "theme": "SSO",
  "relationship": "AFFECTS",
  "properties": {"initiative_status": "planned", "target_period": "end of Q3",
                 "commercial_risk": true},
  "confidence": 0.85
}
```

## Worked example — one signal per initiative, not per sentence

Input:
```
AI authoring: we're shipping the first version this sprint. It'll let PMs
draft a PRD from three bullet points. Targeting general availability by
Q4.
```
Three sentences, ONE initiative — one signal, not three:
```json
{
  "kind": "finding",
  "content": "AI authoring ships its first version this sprint, letting PMs draft a PRD from three bullet points, targeting general availability by Q4",
  "source_type": "pm_manual",
  "theme": "AI authoring",
  "relationship": "AFFECTS",
  "properties": {"initiative_status": "committed", "target_period": "Q4"},
  "confidence": 0.85
}
```

## Worked example — no signal (standalone metric, no initiative attached)

Input:
```
Current state: ARR is $2.0M, up 14% this quarter. Churn is 9% on the
self-serve plan.
```
Output: no signal extracted — neither sentence names a planned initiative;
both are standalone context metrics.

## Worked example — no signal (document-marked shipped section)

Input:
```
## Shipped in Q2 — not part of this roadmap going forward
- Redesigned onboarding checklist
- Usage-based billing
```
Output: no signal extracted — the document itself disowns this section as
already shipped and no longer forward-looking.
