---
name: stakeholder-update
description: Write a targeted async update for a specific stakeholder or audience. Use when the user says "update my stakeholders", "write an update for [exec/team/customer]", "async update", or needs to keep someone informed in their language. Produces an update tailored to one audience's concerns and the action they need to take, in whatever channel fits — a shared doc, an email, a Slack/chat message, or a deck note. Channel is chosen to fit the update, not assumed to be email.
---

# Stakeholder Update

## What it does
Produces an async update written for a *specific* audience's concerns - what this reader cares about, in their language, with the one thing they need to know or do. Unlike a generic status report, it tailors content and framing per audience (exec, peer team, leadership, customer) AND to the channel it'll be delivered in — a shared document, an email, a Slack/Teams message, or a note in a deck. The same update is shaped very differently as a living doc vs. a one-off email vs. a quick chat ping, and the skill picks (or asks for) the channel rather than defaulting to email.

## When to use / when NOT to use
- **Use** to inform a particular stakeholder/group and prompt the right response.
- **Do NOT use** for a standard project status (`status-report`) or a full stakeholder strategy (`stakeholder-map`).

## Inputs
- **Required:** the audience and what you need to convey.
- **Optional:** the audience's known concerns, the action wanted, relationship context.
- **Channel:** doc / email / Slack-or-chat / deck-note. *If the channel isn't given, infer it from the cue (a recurring or shared update → a doc; a quick heads-up → chat; a formal external note → email) or ask — do **not** assume email. Default goal is inform-and-align.*

## Method (methodology)
Audience-first framing + channel fit (mirrors product-on-purpose's 5 channel x 5 audience matrix).
1. **Read the audience** - what do they care about (exec: outcomes/risk; eng: scope/clarity; sales: customer impact/timing; leadership: strategic fit)?
2. **Lead with their concern** - open with what matters to them, not what you did.
3. **The one takeaway** - the single thing they must remember.
4. **The action** - what you need from them (or "no action needed - FYI").
5. **Fit the channel** (chosen, not assumed):
   - **Shared doc** — for recurring or reference updates people return to: a clear title, a TL;DR at top, sections/headers, and (if recurring) a consistent structure so readers can scan week to week. Often the right default for an ongoing program update.
   - **Email** — for a one-off push to a defined list: subject line, skimmable, BLUF, short.
   - **Slack / Teams / chat** — for a quick heads-up: a few lines, direct, the ask up front, no headers.
   - **Deck note / slide** — for an update that lives in a review: one slide's worth, headline + 2–3 supporting points.
6. **Tone** - match the relationship and stakes.

## Output spec
A ready-to-use update **formatted for the chosen channel** (a doc with title/headers, an email with a subject, a chat message, or a slide note) — opening with the audience's concern, carrying one clear takeaway and an explicit action (or FYI). Offers per-audience/channel variants on request, including reshaping the same content for a different channel.

## Sprntly integration (optional)
- **Inputs from Sprntly:** progress/outcomes from the outcome graph; the stakeholder's interests from `stakeholder-map`.
- **Outputs to Sprntly:** the update logged; any commitments/asks tracked.
- **Degrades to:** standalone; ask for audience.

## Quality checklist (the bar)
- [ ] Opens with the audience's concern, not the sender's activity.
- [ ] Carries exactly one clear takeaway.
- [ ] States the action needed (or explicitly "FYI, no action").
- [ ] **Channel was chosen to fit (not assumed to be email)**, and the format matches it — a doc reads as a doc (title/headers/TL;DR), a chat message as a chat message, etc.

## Known gaps / limitations
- It can't know unstated office politics; surface the relationship context for sharper framing.
- Over-updating erodes signal; advise on cadence, not just content.

## Worked example
**Input:** "Tell the VP of Sales the collab feature beta is delayed a week."
- **As an email (one-off):** subject "Co-editing beta: one-week slip, GA still on track." Opens with their concern (renewal risk): the two at-risk enterprise renewals that wanted co-editing, and the timing. Takeaway: beta slips one week to 6/17, GA still on track for the quarter. Action: "Can you hold the renewal calls to the week of 6/17 so they see it live?" Skimmable, no eng detail.
- **As a shared doc (recurring program update):** same content, but with a title ("Co-editing — weekly status"), a TL;DR line, and standing sections (Status / What changed / Impact on renewals / Asks) so Sales can scan it the same way each week — a living reference, not a one-off push.
- The skill picks the channel from the cue (or asks); it does not assume email.
