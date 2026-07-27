# Examples — golden reference and anti-patterns

Compare every brief you generate against the golden reference below. It is the canonical target for voice, title shape, body arc, valence color, and CTA placement. The counter-examples are real failure modes — each one was produced during development and corrected. When self-critiquing (SKILL.md step 6), check that your output matches the golden patterns and avoids every anti-pattern.

---

## Golden reference brief

### Greeting (3 lines, offensive framing, totals = sum of cards)

> Good day, David — I've been through everything across your tools, and roughly **$60M is riding on what surfaced this week**. The clearest signals are a gap a competitor just opened, **$8.4M** sitting with accounts that have outgrown their plan, and friction costing your highest-spend users. Five ranked below; the top three carry the most.

Why it works: addressed by name; says what was looked at; rolls up a total that equals the figures in the cards; names what surfaced rather than what to do about it; exactly three lines.

### Card 1 — Reliability

- **Title:** A login bug is failing 1 in 6 iOS checkouts — roughly $2.2M a year is going with them.
- **Body:** A silent failure at the final checkout step has been crashing the iOS app for three weeks, with no error shown to the customer. Roughly one in six iOS checkouts now fail there, and at current volume that's about **$2.2M in revenue a year**. Based on 4,100 logged exceptions, drop-off isolated to the final step, and a matching gap in billing volume.
- **Sources:** Sentry · Analytics · Billing
- **CTAs:** View the evidence (primary) · Generate PRD (ghost)

Patterns: title = finding (1 in 6 checkouts) + stake ($2.2M going with them), sized rather than promised. Body names its own subject ("a silent failure at the final checkout step"), so it reads with the title removed. Arc = what's happening → what's at stake → what it rests on. No tool list in the prose; the chips carry provenance. Accent = reliability clay.

### Card 2 — Competitive

- **Title:** A rival's new search has cost 3 deals this quarter — with ~$1.6M in renewals raising the same gap.
- **Body:** Since Togal shipped natural-language search last month, it has become a deciding factor in three deals you've lost this quarter, and two more renewals have raised it unprompted. This month's competitive review covers four more moves across pricing and onboarding; this is the one with a number attached.
- **Sources:** Competitor intel · Sales calls · CRM
- **CTAs:** View the full report (primary) · Generate PRD (ghost)

Patterns: finding (3 deals lost) + stake (~$1.6M raising the same gap). The figure is a range because it's a projection, not false precision. The card points at the report rather than compressing it.

### Card 3 — Growth

- **Title:** 42 accounts have outgrown their plan — $8.4M in expansion is sitting unclaimed.
- **Body:** Forty-two of your Team-plan accounts have quietly pushed past every Enterprise seat and usage limit, and several have signaled they're ready to move up. That's **$8.4M in expansion revenue** currently unclaimed. Based on billing thresholds crossed and seat counts as of yesterday.
- **Sources:** Billing · Analytics · CRM
- **CTAs:** View the evidence (primary) · Generate PRD (ghost)

Patterns: every card has a clear bold title — even an opportunity card. The stake is stated as a fact about the present ("is sitting unclaimed"), not a promise about a fix. Accent = growth green (a gain, so a gain color).

### Card 4 — Engagement

- **Title:** 50% of new users never reach the action that drives retention — an ~18-point gap between those who do and don't.
- **Body:** Nearly half of your new users finish setup but never run their first report — the single action most correlated with retention, where those who reach it stay at 87% against 44% for those who don't. Nothing in the product currently points them there. Based on 12 weeks of cohort data across 8,400 signups.
- **Sources:** Analytics · Support
- **CTAs:** View the evidence (primary) · Generate PRD (ghost)

Patterns: a non-monetary stake (a retention-point gap) still uses the finding-then-stake title shape. This is the reference for the title formula.

### Card 5 — Demand

- **Title:** Your top accounts keep asking for real-time collaboration — $50M in renewals sits with them.
- **Body:** Your highest-paying accounts have raised real-time collaboration in every quarterly review this year, and the requests sharpened once two competitors shipped it. Those accounts carry roughly **$50M in renewals**. Drawn from 14 sales calls and a recurring theme across enterprise support threads.
- **Sources:** Sales calls · Support · Competitor intel
- **CTAs:** View the evidence (primary) · Generate PRD (ghost)

---

## Golden — cross-channel customer problem

The highest-value card shape in the skill: three feedback sources, each supplying what it's authoritative for.

- **Title:** Export failures are now your most-raised issue — and $4.1M in renewals sit with the accounts hitting them.
- **Body:** Large CSV exports have been timing out silently since the June storage migration. It's the most-raised issue among your enterprise accounts this month, and the 38 accounts affected hold **$4.1M in renewals**. Interviews suggest why it lands so hard: teams schedule these for month-end close, so a silent failure isn't caught for days. Drawn from 340 support tickets, three interviews, and a public thread.
- **Sources:** Support · Reddit · User interviews
- **CTAs:** View the evidence (primary) · Generate PRD (ghost)

Channel discipline, which is what makes this card defensible:

| Element | Came from | Why |
|---|---|---|
| "most-raised issue among your enterprise accounts" | `voice-of-customer` (internal) | your own customers — the only channel allowed a headline stat about them |
| "$4.1M in renewals" | `voice-of-customer` (internal) | account-level impact needs account-level data |
| "teams schedule these for month-end close" | `interview-synthesis` (direct) | the *why*; no other channel can supply it |
| "all three channels" | corroboration across all three | stated because it's true, and three honest chips back it |

The public channel shaped the ranking — it's loud on Reddit, which is why this outranked two internal issues — but supplied no number. That is exactly right.

### The same finding when only one channel exists

- **Title:** Export failures are the top complaint in support — and $4.1M in renewals sit with the accounts hitting them.
- **Sources:** Support

One chip, no convergence language, and "in support" scopes the claim honestly. The card is not weaker for being single-channel; it's just narrower, and the prose says so.

### The same finding when channels disagree

- **Body opens:** Large CSV exports have been timing out since the June migration. Your own customers rank it third behind billing and search; publicly it is the loudest complaint you have, and the gap is itself worth noting — the people hitting it hardest may not be the ones paying you.

Tension narrated, not averaged. No merged severity number is invented to split the difference.

---

## Golden — competitive card as a pointer

The monthly report is long and lives elsewhere. The card carries one headline finding and sends the reader on.

- **Title:** A rival's new search has cost 3 deals this quarter — with ~$1.6M in renewals raising the same gap.
- **Body:** Since Togal shipped natural-language search last month, it has become a deciding factor in three deals you've lost this quarter, and two more renewals have raised the same gap. This month's competitive review covers four more moves across pricing and onboarding; this is the one with a number attached.
- **Sources:** Competitor intel · Sales calls · CRM
- **CTAs:** View the full report (primary) · Generate PRD (ghost)

Note what it does *not* do: compress a month of analysis into four lines. It names the one finding with an impact figure and links out.

---

## Golden — an `updated` card

Resurfaced because it got materially worse. The first beat is the change, not the background.

- **Title:** The plan-limit gap has spread from 40 accounts to 96 — now $12.6M in expansion sitting unclaimed.
- **Body:** Flagged three weeks ago at 40 accounts, the number of Team-plan customers past every Enterprise seat and usage limit has more than doubled to 96 — taking the unclaimed expansion from $8.4M to **$12.6M**. The jump traces to a single onboarding cohort that landed in June. Based on billing thresholds crossed and seat counts as of yesterday.
- **Sources:** Billing · Analytics · CRM

The change carries the card. Without the delta this finding wouldn't appear at all.

---

---

## Golden — a quiet daily brief

Most days on a daily cadence look like this, and that is correct behavior.

> Quiet day, David — nothing new crossed the line overnight. Three things from earlier this week are still open below.

One line, no cards, no total, no manufactured urgency. Compare the failure mode in counter-example 9.

---

---

## Signal → card transform (worked example)

This is the input that produces Card 1, so you can see the mapping. Note every figure in the card traces to a field here — nothing is invented at writing time.

```json
{
  "id": "sig_checkout_ios",
  "type": "reliability",
  "pain": { "metric": "iOS checkout failure rate", "value": "1 in 6", "context": "final checkout step, silent crash" },
  "value": { "verb": "recover", "metric": "revenue at stake", "amount": "$2.2M", "range": null, "basis": "failed-checkout volume × AOV, annualized", "confidence": 0.9 },
  "story": "A silent failure at the final checkout step has been crashing the iOS app with no error message for three weeks.",
  "recommended_action": "Retry on the failing call plus a clear error state in place of the dead screen.",
  "prd_ref": "prd_1042", "prototype_ref": "proto_1042",
  "sources": ["Sentry", "Analytics", "Billing"],
  "evidence": ["4,100 logged exceptions in 3 weeks", "drop-off isolated to final step", "billing volume gap confirmed"],
  "confidence": 0.94, "urgency": "high",
  "reach": { "unit": "users", "count": null },
  "first_seen": "2026-05-26", "dismissed_before": false
}
```

The title's stake clause ("roughly $2.2M a year is going with them") comes straight from `value.amount`; if `value.amount` were `null`, the title would size the finding qualitatively and no dollar figure would appear. Note the phrasing states what is currently being lost, not what a fix would recover — the finding is reported, the remedy is the reader's call.

---

## Counter-examples — do not produce these

**1. Body that leans on the title (fails self-containment).**
✗ *Body:* "It's been live on iOS for three weeks: Sentry logs the crash, analytics shows the drop-off, and billing confirms the loss."
Why it fails: "It's" has no referent without the title, and the sentence catalogues tools instead of telling the story. Fix: name the subject ("A silent failure at the final checkout step…") and move provenance to the source chips.

**2. A card with no clear title (fails "every card has a title").**
✗ Opening a card with a sentence of context and a standalone number line, with no bold headline.
Why it fails: the reader has nothing to scan. Every card — opportunity or problem — gets a bold pain-plus-value title.

**3. Title with pain but no value (fails the title formula).**
✗ *Title:* "A rival's new search is already in your lost-deal notes — 3 deals gone this quarter."
Why it fails: states the finding, never sizes it. Fix: add the stake clause ("— with ~$1.6M in renewals raising the same gap").

**4. Defensive greeting (fails offensive framing).**
✗ "This week leans defense over offense. About $52M is exposed across reliability, churn, and competitive gaps…"
Why it fails: frames everything as loss to prevent. Fix: lead with upside to capture ("…roughly $60M is within reach").

**5. Meta-widgets at the top (fails "the top must tell the story").**
✗ A "3 signals agree" tag or a 91%-confidence bar as the most prominent element.
Why it fails: that's metadata about the card, not the story in it. The headline does the work; provenance stays quiet in the chips.

**6. Fabricated precision (fails the grounding guardrail).**
✗ *Title:* "…will cost exactly $1,627,400 in renewals."
Why it fails: false precision on a projection, and likely no basis. Fix: ranges over fake decimals ("~$1.6M"), and only if `value` carries a basis.

**7. Forced convergence (fails honest provenance).**
✗ Showing three source chips and writing "multiple signals converged" when one source carried it.
Why it fails: overstates the evidence. A single loud signal (e.g., a 1,000% complaint spike) is allowed to stand alone with one honest chip.

**8. Gain color on a loss (fails valence rule).**
✗ A churn-risk card rendered in growth green.
Why it fails: color must match valence. Losses use their type's warm/cool loss accent; only true gains use green.

**9. Repetition dressed as freshness (fails the freshness gate).**
✗ Day 2 of a daily brief re-runs yesterday's checkout card with the sentence reworded and "today" swapped in for "this week."
Why it fails: nothing changed, so nothing was earned. The finding is `carried` and is simply not surfaced — it stays ranked on the backlog. This is the failure mode that kills a daily digest fastest.

**10. Slow-lane leakage (fails slow-lane discipline).**
✗ The monthly competitive finding rendered as a full card on a Wednesday three weeks after the report ran.
Why it fails: its source hasn't refreshed since the last brief, so it cannot hold a card. Off the brief until the next report lands — unless the upstream skill emitted a genuine interim finding, which enters as `new` on its own merits.

**11. Padding a thin brief (fails restraint and no-padding).**
✗ Two fresh findings exist, so three carried items are promoted to reach the default of five cards.
Why it fails: the default is a ceiling, not a quota. Two cards is a correct brief. At most one carried item may be promoted, and only when there would otherwise be none.

**12. Cadence-blind greeting (fails cadence framing).**
✗ "There's real upside on the table this week" at the top of a daily brief.
Why it fails: the framing contradicts the cadence and quietly signals the brief was written once and reused. Daily leads with what moved since yesterday; monthly leads with the month's picture.

**13. Public forum stat presented as your customer base (fails channel discipline).**
✗ "Forty percent of your users say the export is broken" — sourced from Reddit thread sentiment.
Why it fails: Reddit is not your customer list, and the sentence claims it is. The public channel is authoritative for breadth and velocity, never for a headline stat about your customers. Fix: "Export failures are the loudest complaint about you publicly, and the third most-raised in your own support queue."

**14. Nagging (fails rotation).**
✗ The same unaddressed item as card 4 for the sixth consecutive week.
Why it fails: after three appearances with no action and no change, it retires to the backlog and returns only if it gets materially worse. Persistence past that point trains the reader to skim.

**15. Apologizing for a missing source (fails silent degradation).**
✗ "Reliability signals are unavailable this week because monitoring isn't connected."
Why it fails: the brief is for a reader deciding what to do, not a status page for the pipeline. An unwired category contributes nothing and says nothing.

**16. Prescribing before the reader has judged (fails the insight-first posture).**
✗ *Body ends:* "We've drafted the fix as a PRD; review and approve it to clear the biggest source of complaint you have."
Why it fails: it tells the PM what to do before they've decided the finding is real, and readers experience it as being sold to. The third beat is the evidence base — what this rests on — and the CTA does the inviting. Fix: "Drawn from 340 support tickets, three interviews, and a public thread."

**17. A title that promises the fix (fails the title shape).**
✗ *Title:* "Export failures now top complaints — fixing them protects $4.1M in at-risk renewals."
Why it fails: "fixing them protects" presumes both the solution and the outcome. Size the problem instead: "…and $4.1M in renewals sit with the accounts hitting them."

**18. PRD in the primary slot (fails CTA correctness).**
✗ **View PRD** (primary) · **View the evidence** (ghost).
Why it fails: it inverts the posture. Evidence leads because the reader investigates first; the PRD is what they generate once they agree.

**19. Dumping a report (fails release pace).**
✗ The monthly competitive review lands and all six of its findings card on the same day.
Why it fails: it spends the whole month's material in one cycle, then goes silent for four weeks. On a daily brief, six findings over thirty cycles releases at roughly one every five days — high-urgency findings excepted.

**20. A standing report card (fails the freshness gate).**
✗ A card reading "Your monthly competitive review is ready — read it here," held on the brief for the whole month.
Why it fails: it's the stalest possible object — unchanging by construction. Reports don't get their own card; their strongest finding cards and carries the link.
