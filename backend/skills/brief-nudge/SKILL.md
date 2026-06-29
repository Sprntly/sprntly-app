---
name: brief-nudge
description: Generate the multi-channel notification + reminder sequence that drives a user to open a brief (e.g. the weekly-brief) — Slack and email, a day-0 announcement plus day-1/2/3 reminders sent only if it's still unopened — each leading with the headline impact, a short "what we're seeing" teaser, and ONE strong deep-link CTA into the right workspace and the brief page. Use when the user says "notify users about the brief", "reminder sequence", "nudge users to open X", "Slack + email announcement", "drip reminders", or wants delivery comms that escalate honestly. Channel-aware (compact Slack vs titled, branded email) and day-aware (announce → gentle → focused → final). Never fabricates figures — every number traces to the brief — and never manufactures urgency.
triggers:
  - "notify users about the brief" / "announce the brief"
  - "reminder sequence" / "drip reminders" / "day 1/2/3 reminders"
  - "nudge users to open X" / "drive opens" / "Slack + email for the brief"
when_not_to_use:
  - Writing the brief itself                              -> weekly-brief
  - Briefing leadership on an external event              -> market-event-brief
  - General customer comms / incident / outage messaging  -> customer-comms
inputs:
  required:
    - brief_ref: the workspace + brief id/URL, and the deep link target (workspace → brief page)
    - rollup: the brief's headline figure (total upside)
    - top_items: the highest-impact items, each {label, what_we_see, impact}
  optional:
    - recipient name, brief date / close date, per-item deep links, brand tokens, open-state (drives whether reminders send)
  hard_rule:
    - Every figure traces to the brief. Never invent numbers, never manufacture urgency.
outputs:
  - For each (channel × day): the rendered Slack message and email, sharing one deep-link CTA.
guardrails:
  - ONE dominant CTA per message; per-item links are optional and must never compete with the primary button.
  - Lead every email subject, email title, and Slack headline with the concrete business-impact figure.
  - Figures trace to the brief; no invented numbers.
  - Tone escalates honestly across days; the final reminder promises to pause.
  - The CTA deep-links to the right workspace and the brief page — never a generic home screen.
---

# Brief Nudge — drive users to the brief

Delivery/activation comms that get a surfaced brief actually opened. **One nudge = one brief = one primary action.** This skill writes the messages; it does not write the brief (`weekly-brief`) or run any analysis.

## What it produces
A Day-0 announcement plus up to three escalating reminders (Day 1/2/3, **sent only if the brief is still unopened**), each rendered for two channels:
- **Slack** — compact: a bold first line, ≤3 short lines, one button.
- **Email** — titled and branded: subject + preheader, a header/title, a hero rollup, a scannable "what we're seeing" teaser, one big CTA, and a footer (workspace, manage notifications, unsubscribe).

## When to use / when NOT to use
- **Use** to announce and remind about a brief, digest, or any surfaced artifact across Slack + email with a day-based cadence.
- **Do NOT use** to write the brief (`weekly-brief`), to brief leadership on an external event (`market-event-brief`), or for general/incident customer comms (`customer-comms`).

## Method
1. **Pull the rollup + top items** from the brief; select the 1–3 with the highest impact (pain → what it's worth).
2. **Pick ONE primary CTA** — a deep link to *workspace → brief page*. Per-item links are optional and visually secondary; they never compete with the button.
3. **Day 0 — announce:** greeting + total upside + top 3 teaser + CTA.
4. **Reminders (only if unopened) — escalate urgency honestly, through time + cost-of-waiting, never repetition:**
   - **Day 1 — impact-led:** lead with the figure still on the table, the top 2 items, note both are already drafted, CTA.
   - **Day 2 — focused:** the single biggest item with its figure in the subject/headline, plus the concrete cost of waiting (the window narrowing), CTA.
   - **Day 3 — final:** one item + the figure + the close date ("rolls off Friday") + an honest "we'll pause reminders after this," CTA.
5. **Render per channel** (Slack compact / email titled & scannable) holding layout constant so each message reads the same way.
6. **Honesty pass:** every figure traces to the brief; tone escalates without fear-mongering; the stop-reminding promise is kept.

## Channel × cadence at a glance
| | Day 0 — announce | Day 1 | Day 2 | Day 3 — final |
|---|---|---|---|---|
| **Slack** | total + top 3 + button | total + top 2 | biggest item + cost of waiting | one item + close date + pause note |
| **Email** | subject + title + hero + 3 items + CTA | "still waiting" + top 2 + CTA | focused subject + one item + CTA | "last reminder" + one item + pause note + CTA |

## Output spec
- **Slack:** `{ headline, intro, items[], primaryCTA(deeplink) }` — one button; deep link shown/attached.
- **Email:** `{ subject, preheader, eyebrow, title, intro, items[], primaryCTA(deeplink), perItemLinks?(secondary), footer }`.
- The CTA always routes to **workspace → brief page**. Day 3 includes the close date and the pause-reminders note.

## Quality checklist (the bar)
- [ ] Exactly one dominant CTA per message; per-item links never outweigh it.
- [ ] Every email subject, email title, and Slack headline leads with the concrete business-impact figure.
- [ ] Day 1→3 escalate urgency through time pressure and cost-of-waiting, not repetition; no manufactured scarcity.
- [ ] Every figure traces to the brief; no invented numbers; no manufactured urgency.
- [ ] Slack stays compact (one button); email has a subject **and** a title and is scannable.
- [ ] Reminders escalate honestly; the final one promises to pause and states the close date.
- [ ] The CTA deep-links to the correct workspace and the brief page, not a generic home.
- [ ] Reminders only fire while the brief is unopened.

## Known gaps / limitations
- It writes the messages; the open-state gating and the actual send/deep-link routing are the platform's job.
- It is only as truthful as the brief it cites — it surfaces figures, it does not validate them.

## Worked example (abridged)
**Input:** `weekly-brief` rollup ~$60M; top items — competitive ($1.6M renewals), expansion ($8.4M), checkout ($2.2M); deep link `/w/acme/brief/jun-23`.
**Output:** Day-0 email subject *"Your weekly brief: ~$60M in upside is on the table"* + Slack one-liner with the three teaser items and a single **Open this week's brief** button; Day-2 narrows to the $8.4M expansion play; Day-3 email *"Last reminder — your weekly brief closes Friday"* with the pause-reminders note. Every CTA deep-links to the Acme workspace → this week's brief.

## Files in this skill
```
brief-nudge/
├── SKILL.md            # this file — authoritative behavior spec
├── README.md           # orientation + how-to-use guide
└── examples/
    ├── preview.html    # rendered gallery of all 8 messages (open in a browser; switch days)
    └── messages.md     # the full copy of every message — read to copy the pattern
```

## Reference examples
`examples/preview.html` renders the full sequence (Slack + email × Day 0–3) with a day switcher; `examples/messages.md` holds the copy for every message so the pattern can be reproduced directly. They hold the layout constant while the item count shrinks (3 → 2 → 1 → 1) and the tone escalates (announce → impact-led → cost-of-waiting → final). Read them to calibrate before composing a new sequence.
