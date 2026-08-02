# Capture contract — Stage 1 of `voice-of-customer-report`

> **This is a capture contract, not an analysis guide.** It governs what you pull out of a source and how you record it. It says nothing about what any of it means. That judgment happens in Stage 2 (the report), after capture is complete.

## Where this sits

```
raw sources                 STAGE 1 — CAPTURE            STAGE 2 — REPORT
(calls, tickets,     →      this document          →     SKILL.md
 emails, chat,              one record per                themes, counts,
 surveys, notes)            mention, no counts,           frustration, ranking,
                            no ranking, no summary        recommendations
```

**Division of labor.** Stage 1 owns the boundary between raw input and structured signal. Stage 2 owns everything past it: reading across records, weighing evidence, spotting patterns, deciding what matters, recommending action.

**Do not let analysis leak backward into capture.** A record's value is not a capture-time decision. If you find yourself judging importance while still reading a transcript, stop and finish capturing.

**Work in this order.** Capture first, in full, across every source. Only then analyze.

## Invariants — do not adapt these

They exist because violating them destroys information that cannot be recovered later.

1. **One record per mention.** Never merge, collapse, or deduplicate.
2. **Preserve verbatim** wherever the source contains words.
3. **Attribute relayed claims to the named account**, not the person relaying them.
4. **Populate churn and deal risk only when stated**, never when inferred from tone.
5. **Record a reason for anything not captured.**
6. **Prefer `undetermined` or `other` over a guess.**

Adapt freely otherwise. Sources vary and this document cannot anticipate all of them. Extend the artifact kinds, add signal types, adjust reasoning for a format not foreseen here — and note what you changed and why. **The schema is a floor, not a ceiling: extra fields are welcome, missing required fields are not.**

When a case is not covered, fall back to these invariants and the guardrails at the end. When a source is genuinely unreadable or ambiguous, capture what is there, mark the gaps, and move on. Do not stall and do not invent.

## Scope of this stage

**In scope:** deciding whether an artifact is worth reading · judging whether it is a customer or internal discussion · identifying who is making each claim · deciding which claims are product-relevant · capturing each claim with context and enrichment · tagging which team owns it.

**Out of scope — do not do these here:** counting or computing prevalence · ranking or prioritizing · merging or deduplicating records · generating recommendations · assessing which issues matter most.

**Never merge records.** Every distinct mention becomes its own record with its own verbatim, speaker, account and date — even when three people say the same thing, and even when the same person says it three times. Preserving each mention is what allows exact quotation later and lets Stage 2 count accurately. Collapsing destroys both.

---

## Step 1 — Read the artifact and reason about what it is

Do not rely on file names, meeting invites, calendar titles or attendee lists. They are frequently missing, wrong or ambiguous. Read the content and form a judgment.

**What kind of artifact is this?**
`live_conversation` · `support_ticket` · `email` · `chat` · `survey_response` · `community_post` · `meeting_notes` · `secondhand_summary` (someone relaying a customer conversation they had) · `other`

Use `other` freely. If the artifact does not clearly fit, classify it as `other` and describe what it appears to be in a note. Nothing is dropped for failing to fit a category. Do not force-fit. Metadata, when present, is a hint. **Content is the decision.**

**Is this a customer discussion or an internal one?**

Read the whole artifact and reason it through. Record the judgment and a one-line rationale.

| Suggesting **external** | Suggesting **internal** |
|---|---|
| Second-person product reference — *your tool*, *you guys*, *does your platform* | No second-person reference anywhere — everyone speaks as an owner |
| Introductions establishing names, roles, company | No introductions — participants already know each other |
| One party describes using it; the other explains, demos, troubleshoots | Shorthand for internal systems, teams, sprints, people without explanation |
| Discovery questions running in one direction | Discussion of roadmap, staffing, process, strategy rather than usage |
| Onboarding-level explanation of things an employee would already know | |

These are indicators, not a checklist. A conversation with no introductions can still be external; a conversation naming a customer can still be internal. **Weigh the whole artifact.** For tickets, email and chat, apply the same reasoning: is this a customer describing their experience, or an employee discussing work?

When genuinely ambiguous, record `undetermined` with a rationale and continue to extraction. Do not guess. Stage 2 decides how much weight to place on it.

## Step 2 — Determine who is making each claim

Speaker identity is often unavailable and is not required. What matters is **the origin of the claim**.

| Tier | Origin |
|---|---|
| `direct` | The affected user describing their own experience, unprompted |
| `elicited` | The affected user agreeing to or answering a leading question from the vendor side |
| `relayed` | Someone reporting a named account's words — including internal summaries and secondhand accounts. **Attribute to the named account, not the reporter** |
| `asserted` | A vendor-side speaker claiming market truth with no named source — *"customers keep asking for this"* |
| `speculative` | Anyone hypothesizing about future or possible needs |
| `undetermined` | Cannot be established from the artifact |

`relayed` is what makes internal artifacts usable — *"Acme said SSO is blocking them"* is evidence about Acme. **`asserted` and `elicited` are the two claim types most often mistaken for customer evidence:** one is a rep speaking for customers who may not exist, the other is demand created by the question that prompted it. Capture both, label both accurately, and let Stage 2 decide their weight.

## Step 3 — Decide what is relevant

**Capture** anything a product or engineering team could act on: feature requests · bugs and defects · usability friction · performance and reliability problems · integration gaps · things users could not find that already exist · missing or unclear documentation · workarounds users built to compensate.

Capture from customer conversations, sales and prospect calls, POC and pilot customers, support tickets, email, chat, surveys, community posts, and internal artifacts containing a named account plus reported speech.

**Do not capture:** pure scheduling and logistics · recruiting · vendor pitches · investor and board discussions · all-hands and training · bot-generated tickets, autoresponders, spam · test and demo account activity · internal discussion with no named-account attribution.

**Exclude before reading further:** HR and personnel matters · compensation · legally privileged material · live security incidents · PII, PHI, payment data · material non-public information · any account contractually barred from cross-account analysis.

Record a `reason_code` for anything not captured. **Never drop silently** — an unexplained omission cannot be reviewed or corrected.

## Step 4 — Capture each mention

**One record per mention. Never merge.**

```json
{
  "verbatim": "exact words as spoken or written",
  "normalized": "one-line plain statement of the issue",
  "type": "feature_request | bug | usability | performance | reliability |
           integration | discoverability | documentation | workaround | other",

  "artifact": {
    "kind": "live_conversation | support_ticket | email | chat | survey_response |
             community_post | meeting_notes | secondhand_summary | other",
    "setting": "customer | internal | undetermined",
    "setting_rationale": "one line on why",
    "date": "when it was said, not when ingested"
  },

  "origin": {
    "tier": "direct | elicited | relayed | asserted | speculative | undetermined",
    "volition": "volunteered | prompted | undetermined",
    "speaker_note": "role or side if inferable, else null"
  },

  "account": {
    "name": "as spoken, or null",
    "lifecycle_stage": "prospect | poc | onboarding | production | at_risk | churned | undetermined",
    "size_segment": "stated or independently known only, else null"
  },

  "intensity": {
    "sentiment": "neutral | frustrated | angry | resigned | enthusiastic",
    "user_impact": "blocking | major_friction | minor_friction | cosmetic | unstated",
    "cadence": "constant | daily | weekly | occasional | one_off | unstated",
    "workaround_present": true,
    "workaround_description": "what they built or do instead"
  },

  "business_risk": {
    "churn_signal": "stated | none",
    "deal_blocker": "stated | none",
    "expansion_blocker": "stated | none",
    "evidence": "verbatim supporting any non-none value"
  },

  "owner": "product | design | engineering | sales | marketing | customer_success |
            support | enablement | pricing | security"
}
```

### Field guidance

- **`verbatim`** is mandatory when the source contains words. For paraphrased sources — meeting notes, secondhand summaries — capture the closest available rendering and note that it is a paraphrase. **Paraphrased records are still first-class**; they are not downgraded.
- **`type`** — check whether the capability already exists before classifying something as `feature_request`. *"I couldn't find a way to export"* is often `discoverability`, not a missing feature. **This distinction is the most common source of wasted roadmap items.**
- **`workaround_present`** is the highest-value field here. A spreadsheet, script or manual process built to compensate is revealed behavior rather than stated preference, and **the workaround usually specifies the real requirement better than the request does**. Capture the description whenever present.
- **`cadence`** is what the user says about their own experience — *"every single time"*, *"a few times a week"*. It is **not** a count of how often the issue appears across artifacts. That is Stage 2's job.
- **`business_risk`** — stated only. Populate `churn_signal` only on explicit language: evaluating alternatives, considering not renewing, escalated to leadership. Populate `deal_blocker` only on explicit conditionality: *"we can't sign without this"*, *"this came up in security review"*. **Frustration is not churn risk.** If tempted to infer, leave `none` and let `intensity` carry the concern.
- **`lifecycle_stage`** — when unstated, infer from tense. Conditional/future usage language (*once we roll out*, *before we go live*) with setup vocabulary (*sample data*, *sandbox*, *pilot users*) suggests `poc`. Present and past usage (*we use it for*, *last week it broke*) suggests `production`. When ambiguous, `undetermined`.
- **`size_segment`** — stated or independently known only. **Do not infer company size from how someone speaks.**

## Step 5 — Tag the owner

Every captured signal gets one owner. Nothing is discarded for belonging to another team — tagging lets Stage 2 filter.

The test is **"can product change this?"** — not "can engineering build this." The narrower test wrongly excludes discoverability, documentation, and capabilities gated behind a plan the customer cannot buy.

| Product-owned | Owned elsewhere |
|---|---|
| Feature requests, bugs, usability friction | Pricing and budget constraints → `pricing` |
| Performance, reliability, integration gaps | Procurement, contracts, legal → `sales` |
| Discoverability and documentation gaps | Support responsiveness or quality → `support` |
| Observed workarounds | Customer-side config, permissions, process → `customer_success` |
| | Third-party system failures → `customer_success` |
| | Positioning or messaging confusion → `marketing` |
| | Rep knowledge gaps → `enablement` |
| | Security questionnaire gaps → `security` |

A competitive mention can legitimately produce **more than one record** — a capability gap for product and a positioning gap for marketing are different signals from the same words. Create both rather than forcing a choice.

## Output of this stage

A **flat list of records**, in the order they appeared in the source, plus:
- Artifacts read, with the setting judgment and rationale for each
- Anything not captured, with its `reason_code`

**No summary. No counts. No ranking.** Stage 2 consumes this list.

## Guardrails

**Never infer:** churn or deal risk from sentiment · company size from speech · that a capability is missing without checking whether it exists · cadence from how often something appears across artifacts.

**Never merge, collapse or deduplicate records.** Repetition is data.

**Prefer `undetermined` to a guess.** Every field supports it. An honest gap is correctable; a confident error is not.

**When something does not fit a category, use `other` and describe it.** No artifact and no signal is discarded for failing to match a predefined bucket.

---

## Handoff to Stage 2 — what the report does with these fields

Stage 1 does not rank, but the fields it produces are exactly what Stage 2 ranks on. The mapping is fixed:

| Capture field | What the report does with it |
|---|---|
| `verbatim` + `account.name` + `artifact.date` | The sourced quotes. A theme with no verbatim gets an explicit quote-gap flag, never a manufactured quote. |
| `normalized` + `type` | Theme grouping. `discoverability` and `documentation` are kept distinct from `feature_request` so they aren't scoped as new builds. |
| `origin.tier` | **Which records count.** See the counting rule below. |
| `account.name` | The denominator. Themes are sized in **accounts**, deduped at report time — never in raw mentions, which would let one loud account dominate. |
| `intensity.sentiment` + `user_impact` + `cadence` + `workaround_present` | The 1–5 frustration score and the tone read. A built workaround raises frustration regardless of stated sentiment: it is revealed cost. |
| `business_risk.*` (stated only) | The churn column and the $ at risk column. Empty is empty — never backfilled from tone. |
| `account.lifecycle_stage` | Segment filters and the churn/at-risk attribution. |
| `owner` | Whether a theme reaches the recommendations at all, or is routed to `pricing-packaging`, `enablement`, `marketing` and named as not-product-owned. |

### The counting rule

Not every captured record is customer evidence. The report counts by origin tier:

| Tier | Counted in theme sizes? | Quotable? |
|---|---|---|
| `direct` | Yes | Yes |
| `relayed` | Yes — attributed to the named account | Yes, marked as relayed |
| `elicited` | Yes, **but flagged** — the count line notes how many were prompted | Yes, marked as prompted |
| `asserted` | **No** | No — there is no customer behind it |
| `speculative` | **No** | No |
| `undetermined` | **No** | No |

Excluded records are not deleted, and their exclusion is disclosed: the report states **records captured vs records counted** and gives the reason breakdown in a note beneath the at-a-glance table. A theme that survives only on `asserted` records is reported as an internal belief with no customer evidence behind it — which is a finding in its own right.
