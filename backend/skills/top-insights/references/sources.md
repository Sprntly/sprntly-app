# Sources — category registry and adapter contracts

Every category is fed by an adapter. An adapter's only job is to read an upstream skill's saved output and return `finding` objects in the shape defined by `signal-schema.json`. Adapters do not analyze, score, or compute — if a number isn't in the upstream output, it doesn't exist.

Fields marked **`⟨TBC⟩`** are unresolved and must be filled before the skill runs against real data. They are listed together at the end.

---

## Registry

Most of these produce a **report** containing several findings, not a single insight. See SKILL.md step 2 — reports decompose, and the release pace spreads their findings across the refresh interval.

| Category | Upstream skill(s) | Runs | Output location | Status |
|---|---|---|---|---|
| `customer_problems` | `voice-of-customer-report` ✓, `interview-synthesis` ✓, `public-feedback-report` ✓ | weekly / ad hoc / weekly | ⟨TBC⟩ | live |
| `competitive` | `competitive-intelligence-review` ✓ | monthly | ⟨TBC⟩ | live |
| `reliability` | monitoring-analysis | daily (target) | ⟨TBC⟩ | **planned** |
| `core_metric` | metric-movement | daily (target) | ⟨TBC⟩ | **planned** |
| `celebrate` | ship-impact + resolved findings from this skill | weekly | ⟨TBC⟩ | **planned** |
| `what_to_build` | *(synthesis — no upstream)* | per brief | — | live |

**Planned** means the adapter returns an empty list today. The skill must run correctly with it absent and light up when it's wired, with no other change.

---

## `customer_problems` — three channels, one card

The only category with multiple upstream skills. Each channel is authoritative for a different part of the card; see SKILL.md step 2 for the composition rules. Summarized:

| Channel | Skill | Surfaces | Authoritative for | Never used for |
|---|---|---|---|---|
| `internal` | `voice-of-customer-report` ⟨unconfirmed⟩ · **weekly** | support tickets, Slack, sales/support calls | the pain stat, reach, account-level impact | — |
| `public` | `public-feedback-report` ✓ · **monthly** | Reddit, social, review sites, forums | breadth, velocity, spread beyond your base | a headline stat about *your* customers |
| `direct` | `interview-synthesis` ✓ · **ad hoc** | user interviews | the cause — the *why* beat of the body | volume or reach claims (n is too small) |

**Clustering.** Findings from different channels merge into one when they describe the same underlying problem — same surface plus same failure, not same wording. Prefer matching on a `theme_key` from the upstream skill; fall back to semantic clustering and record every member id in `cluster_members` so the merge is auditable.

**Three channels, three clocks.** Internal refreshes weekly, public monthly, direct only when interviews happen. A merged finding therefore carries three different `as_of` dates; **freshness is governed by the most recent contributing channel.** When the weekly internal run adds corroboration to a finding the monthly public report already raised, that's a candidate material change — reach went up, or a new channel joined — and it may re-card as `updated`. When it adds nothing, it doesn't.

Ad-hoc direct input needs care: an interview batch landing mid-month can materially deepen a finding without changing any number. Treat a newly-added channel as a material change in its own right, since "we now have interviews explaining why" is genuinely new information for the reader.

**Availability.** A recipient may have one, two, or all three. Adapter returns whatever exists. Ranking must not systematically favor multi-channel findings beyond the honest confidence boost (+0.05 per corroborating channel, capped 0.95) — a single-channel finding with a hard number can and should outrank a three-channel finding with none.

**Conflict.** When channels disagree on severity or ranking, set `tension` on the merged finding. The body narrates it. Never average.

**PII.** Public-channel findings carry usernames and quotes. Strip them at the adapter boundary — themes and counts pass through, identities do not, unless the workspace explicitly permits.

---

## `competitive` — monthly, link-out

One upstream skill, run monthly, producing a detailed report. The card is a **pointer**: one headline finding, its value, and a link to the report. Do not compress the month into four lines.

**Cadence.** `refresh_interval_days: 30`. On weekly and daily briefs this is a slow-lane source — a card in the cycle right after the report lands, then absent until the next report. This is the single most likely place for stale content to leak in; the lane rule is what prevents it.

**Mid-month events.** If a competitor does something notable between reports and the upstream skill emits it as an interim finding, it enters as `new` and gets a card. Only report-level output is bound to the monthly cadence.

**The adapter must return every finding in the report, not just the headline.** The report decomposes: its strongest finding cards on the drop, the rest release across the month at the release pace (SKILL.md step 2). If the adapter only surfaces the top one, there's nothing to release and the category goes silent for four weeks. Return the report's own ranking; don't re-rank here.

Each finding must carry `report_ref` and the report's `as_of`, so every card that came out of the report can link back to it.

**The adapter must also return the report's own one-sentence summary** for the shelf row. This is a verbatim pass-through, never composed downstream. Same applies to the voice-of-customer, public-feedback, and interview-synthesis adapters — every report-producing skill needs a top-line the shelf can render.

---

## `reliability` — planned, daily

Reads the recipient's connected monitoring. Contract for when it's built:

- Returns the top incidents or degradations with cause, affected surface, user/revenue reach, and an impact figure where the upstream analysis can derive one.
- If it can auto-generate a patch for the simple cases, it sets `patch_ref`, which switches the card CTAs to **Review the fix** / **View the incident**.
- Sets `status: resolved` once an incident closes, which vacates the card slot and makes it a `celebrate` candidate.
- **Noise control matters more here than anywhere else.** A daily source with a low bar will dominate every brief. The adapter must return only incidents that clear a severity bar ⟨TBC⟩, not everything the monitor saw.

---

## `core_metric` — planned, daily

Tracks the one metric the recipient named as their core goal metric. Returns the current value, the movement, the period, and — where the upstream analysis identifies it — the dominant driver of the movement.

Favorable movement takes the `momentum` type; unfavorable takes the type of the dominant driver. A metric card with no identified driver is a statement, not a recommendation: it may appear, but it cannot claim a value-of-acting figure it doesn't have.

---

## `celebrate` — planned, weekly

Two feeds:
1. **Ship impact** — things shipped that measurably worked. Needs a before/after figure from the upstream analysis; a ship with no measured effect is not a celebration.
2. **Resolved findings from this skill** — when a card the recipient acted on reaches `resolved`, it becomes a celebrate candidate automatically. This closes the loop: today's reliability card is next month's win, and it's the only feed that works with no new skill built.

Cap at one card. A digest that celebrates more than it recommends has lost the plot.

---

## `what_to_build` — synthesis

No adapter. Runs last, over the gated and ranked findings from every other category, and answers: given the recipient's stated core business goal, what are the top three to five things to build?

Requires `core_goal` in `brief_config`. Without it, the block states that the goal isn't set rather than inventing one. Each item backlinks to the finding ids it rests on, and may not introduce a figure that isn't already on one of them.

---

## Adapter contract

Every adapter, live or planned, returns:

```json
{
  "category": "customer_problems",
  "source_skill": "voice-of-customer-report",
  "as_of": "2026-07-21T09:00:00Z",
  "refresh_interval_days": 7,
  "available": true,
  "report": {
    "ref": "https://…/reports/voc-2026-w29",
    "summary": "Export reliability displaced billing as the top theme…",
    "ran_at": "2026-07-21",
    "next_run": "2026-07-28"
  },
  "findings": [ /* ALL of them — not just the headline */ ]
}
```

`report` is optional (a source may emit findings without a standing report) but when present it drives the report shelf. `report.summary` is **verbatim** from the report — never composed downstream.

- `as_of` is **when the upstream analysis ran**, not when it was fetched. Every freshness decision depends on this being right.
- `available: false` means unreachable or not wired — the category contributes nothing and is not mentioned in the brief.
- An empty `findings` array with `available: true` means the source ran and found nothing. That is a real, reportable quiet result; being unable to look is not.
- Adapters never fill a missing figure with an estimate. Absent stays absent.

---

## Open items ⟨TBC⟩

1. **Skill names — partially resolved.**

   | Role | Name | State |
   |---|---|---|
   | Competitive | `competitive-intelligence-review` | ✓ confirmed |
   | Direct channel | `interview-synthesis` | ✓ confirmed |
   | Internal channel | `voice-of-customer-report` | ✓ confirmed (2026-07-26) |
   | Public channel | `public-feedback-report` | ✓ confirmed (Apurva, 2026-07-26) |

   **Resolved:** `public-feedback-report` is the one public-channel source. `feedback-synthesis` (which also exists as a vendored skill) must NOT be registered as a `customer_problems` source — a second reader of the same external surfaces would ingest the same complaint twice and award a corroboration boost that was never earned.

2. **Where each skill saves its output** — path, table, or API, and the shape it saves in. Specifically: does each report expose its individual findings as separate addressable items, or only as prose? Decomposition depends on the former.

   Also needed: **a stable URL per report** for the link-out CTA, and **an evidence view per finding** for the primary CTA on non-report findings.
3. **Whether upstream outputs carry a stable finding id and a `theme_key`.** Without stable ids there is no ledger, and without a ledger there is no freshness logic — this is the load-bearing dependency.
4. **How a recipient's subscription, cadence, core goal, and selection profile are captured and stored.**

   The **selection profile** is the biggest of these and the newest. The reader states what they care about; the skill compiles that into hard filters, weighted preferences, and a focus, and shows the compiled version back for confirmation so they always see what their sentence became. Questions it raises:
   - Where does the reader write it — onboarding, a settings screen, or in conversation with the agent?
   - Is the compiled form editable directly, or only through the statement?
   - Where is it versioned? The audit trail records which profile version produced each brief, so a reader can ask why a past brief looked the way it did.
   - Is there a sensible default profile for someone who hasn't written one? Without it the skill falls back to the base formula, which is a reasonable but generic ranking.
5. **Where the ledger and backlog persist between runs.**
6. **What counts as "acted on"** — is PRD approval an event this skill can read? That drives `in_progress` and resolution.
7. **Severity bar for the reliability adapter**, and which monitoring vendors are in scope.
8. **Whether the product has a backlog page to link to** — everything not carded lives there, so it's the reader's escape hatch. If there's no such page yet, nothing breaks; the backlog is still emitted as data.
