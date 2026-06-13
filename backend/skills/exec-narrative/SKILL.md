---
name: exec-narrative
description: Craft a persuasive executive narrative or strategic memo. Use when the user says "write an exec narrative", "memo for leadership", "make the case to the CEO", "board update narrative", or needs to persuade senior leaders. Produces a SCQA/BLUF-structured argument that leads with the answer and builds the case for a decision.
---

# Executive Narrative

## What it does
Builds a persuasive narrative for senior leaders: it leads with the conclusion/ask (BLUF), frames the situation and the tension that demands a decision (SCQA), marshals the evidence, handles the obvious objections, and closes on the specific decision wanted. It's written for readers who are time-poor, skeptical, and decision-oriented. The output is a **narrative document by default** — a reviewable doc / read-ahead / one-pager — not an email. It only takes a memo or email form (To/From/Re, subject line) when that's explicitly the channel; otherwise it's formatted as a document people open and read, with a title and headers, or as a slide narrative if it's going into a deck.

## When to use / when NOT to use
- **Use** to persuade leadership/board on a strategic decision, investment, or direction.
- **Do NOT use** for routine status (`status-report`) or a single stakeholder note (`stakeholder-update`).

## Inputs
- **Required:** the decision/ask and the reasoning behind it.
- **Optional:** the audience's priorities, the data, anticipated objections, the alternative they might prefer, and the **format/channel** (reviewable doc / board read-ahead / one-pager / email-memo / slide narrative). *If missing, draft the argument and flag where evidence must be filled in. Default the format to a **reviewable document** (title + headers, no To/From/Re) — do **not** assume an email/memo unless told it's being sent as one.*

## Method (methodology)
BLUF + SCQA (Situation-Complication-Question-Answer) + objection pre-handling + Minto pyramid.
1. **BLUF** - open with the recommendation/ask and the headline reason. Execs read the top and decide whether to read on.
2. **SCQA frame** - Situation (shared context), Complication (what changed/the tension), Question (the decision this forces), Answer (your recommendation).
3. **Pyramid support** - the 2-4 reasons, each backed by evidence; structure so skimming the topic sentences carries the argument.
4. **Pre-handle objections** - name the strongest counterargument and address it (credibility).
5. **The alternative** - acknowledge the option they'd otherwise pick and why yours is better.
6. **Close on the decision** - exactly what you're asking them to approve, and the consequence of delay.
7. **Fit the format** - default to a **reviewable doc**: a title that states the decision, then the narrative under light headers (no To/From/Re/subject). Use memo/email headers only if it's actually being emailed; use a slide-narrative shape if it's going into a deck. The argument structure is the same; only the wrapper changes.

## Output spec
A persuasive narrative — **formatted as a reviewable document by default** (a title stating the decision + the argument under light headers; no email/memo headers unless it's actually being sent as email): BLUF recommendation · SCQA-framed setup · pyramid of reasons + evidence · pre-handled top objection · the alternative considered · a crisp decision ask + cost of inaction. Can be reshaped into an email-memo or a slide narrative on request.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the supporting data/outcomes; prior related decisions; `competitive-intelligence-review`/`market-structure` for context.
- **Outputs to Sprntly:** the narrative as an artifact; the decision + reversal conditions recorded.
- **Degrades to:** standalone; flag evidence gaps.

## Quality checklist (the bar)
- [ ] Opens with the ask + headline reason (BLUF), not a slow build.
- [ ] Topic sentences alone carry the argument (pyramid).
- [ ] The strongest objection is pre-handled.
- [ ] Closes on a specific, approvable decision + cost of inaction.
- [ ] **Formatted to the right wrapper** — a reviewable doc by default (title + headers), not an email/memo unless that's the actual channel.

## Known gaps / limitations
- Persuasive structure can't rescue a weak underlying case - pressure-test with `red-team-review` first.
- Exec preferences vary; if the audience favors data-first vs narrative-first, adapt.

## Worked example
**Input:** "Convince leadership to fund a second engineering pod for the platform."
**Output (abridged, as a reviewable doc titled "Fund a second platform engineering pod"):** BLUF: "Recommend funding a 2nd pod now; without it, the Q3 enterprise commitments slip and we risk two renewals." SCQA: Situation - platform demand is up; Complication - one pod is at capacity and enterprise deals depend on platform features; Question - fund a pod or cut scope; Answer - fund it. Reasons: pipeline value > pod cost (evidence), capacity math, renewal risk. Objection pre-handled: "hire later" - shows the ramp lag misses Q3. Ask: approve 3 hires this month.
