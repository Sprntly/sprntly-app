# Examples — golden reference and anti-patterns

Compare every brief you generate against the golden reference below. It is the canonical target for voice, title shape, body arc, valence color, and CTA placement. The counter-examples are real failure modes — each one was produced during development and corrected. When self-critiquing (SKILL.md step 6), check that your output matches the golden patterns and avoids every anti-pattern.

---

## Golden reference brief

### Greeting (3 lines, offensive framing, totals = sum of cards)

> Good day, David — I've scouted everything across your tools, and there's real upside on the table this week: roughly **$60M in revenue is within reach**. The strongest plays are closing a gap a competitor just opened, capturing **$8.4M** from accounts already primed to expand, and clearing the friction costing your highest-spend users. Five ranked below; the top three move the most.

Why it works: addressed by name; leads with work-done + upside; rolls up a total that equals the figures in the cards; names the top plays; frames money to capture, not fires to fight; exactly three lines.

### Card 1 — Reliability

- **Title:** A login bug is failing 1 in 6 iOS checkouts — the fix recovers about $2.2M a year.
- **Body:** For three weeks, a silent failure at the final checkout step has been crashing the iOS app with no error message, and roughly one in six iOS checkouts now fail there — about **$2.2M in lost revenue a year**. We've already drafted the fix as a PRD: a retry on the failing call and a clear error state in place of the dead screen. Review and approve it to put the recovery in motion.
- **Sources:** Sentry · Analytics · Billing
- **CTAs:** View PRD (primary) · View prototype (ghost)

Patterns: title = pain (1 in 6 checkouts) + value of acting (recovers $2.2M). Body names its own subject ("a silent failure at the final checkout step"), so it reads with the title removed. Arc = why → worth → "we've drafted it, review and approve." No tool list in the prose. Accent = reliability clay.

### Card 2 — Competitive

- **Title:** A rival's new search has cost 3 deals this quarter — matching it protects ~$1.6M in at-risk renewals.
- **Body:** Since Togal shipped natural-language search last month, it's become a deciding factor in three deals you've lost this quarter, with two more renewals raising the same gap — and left alone, it will quietly cap your win rate. We've scoped the answer as a PRD, built around the queries your customers actually run and sized to ship before Q3 renewals. Review and approve it to close the gap.
- **Sources:** Competitor intel · Sales calls · CRM

Patterns: pain (3 deals lost) + value (protects ~$1.6M). The value is a range ("~$1.6M") because it's a projection, not false precision.

### Card 3 — Growth

- **Title:** 42 accounts have outgrown their plan — claiming them adds $8.4M in expansion.
- **Body:** Forty-two of your Team-plan accounts have quietly pushed past every Enterprise seat and usage limit, and several have already signaled they're ready to move up — that's **$8.4M in expansion revenue** sitting unclaimed. We've drafted the in-product upgrade flow as a PRD, so the conversation starts itself the moment an account crosses the line. Review and approve it to start capturing it.
- **Sources:** Billing · Analytics · CRM

Patterns: every card has a clear bold title — even an opportunity card. Accent = growth green (a gain, so a gain color).

### Card 4 — Engagement

- **Title:** 50% of new users never reach the action that drives retention — guiding them there could lift it ~18%.
- **Body:** Nearly half of your new users finish setup but never run their first report — the single action most correlated with retention, where those who reach it stay at 87% against 44% for those who don't. The drop-off is simply that no one points them there. We've drafted a guided first run as a PRD; review and approve it to lift retention from the very top of the funnel.
- **Sources:** Analytics · Support

Patterns: non-monetary value (a retention-point lift) still uses the pain-then-value title shape. This is the reference for the title formula.

### Card 5 — Demand

- **Title:** Your top accounts keep asking for real-time collaboration — building it protects $50M in renewals.
- **Body:** Your highest-paying accounts keep asking for real-time collaboration, and the requests have sharpened now that two competitors have shipped it. With roughly **$50M in renewals tied to these accounts**, the gap is becoming a reason to look elsewhere. We've scoped the first version as a PRD — review and approve it to answer the request before it hardens into churn.
- **Sources:** Sales calls · Support · Competitor intel

---

## Signal → card transform (worked example)

This is the input that produces Card 1, so you can see the mapping. Note every figure in the card traces to a field here — nothing is invented at writing time.

```json
{
  "id": "sig_checkout_ios",
  "type": "reliability",
  "pain": { "metric": "iOS checkout failure rate", "value": "1 in 6", "context": "final checkout step, silent crash" },
  "value": { "verb": "recover", "metric": "lost revenue", "amount": "$2.2M", "range": null, "basis": "failed-checkout volume × AOV, annualized", "confidence": 0.9 },
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

The title's value clause ("recovers about $2.2M a year") comes straight from `value.amount` + `value.verb`; if `value.amount` were `null`, the title would fall back to a qualitative value and no dollar figure would appear.

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
Why it fails: states the pain, never says what acting is worth. Fix: add the value clause ("— matching it protects ~$1.6M in at-risk renewals").

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
