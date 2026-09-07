---
name: customer-comms
description: Write customer-facing communications, including difficult ones. Use when the user says "write to customers", "announce X to users", "incident/outage email", "deprecation notice", "price change email", "breaking change comms", or needs to communicate something externally. Produces clear, honest, trust-preserving customer messages tuned to the situation's sensitivity.
---

# Customer Comms

## What it does
Writes external customer communications - especially the hard ones (outages, deprecations, price changes, breaking changes, delays) - in a way that's clear, honest, and trust-preserving. It leads with what the customer needs to know and do, takes appropriate ownership without over-apologizing or over-promising, and matches tone to how sensitive the situation is.

## When to use / when NOT to use
- **Use** for any message going to customers/users, particularly sensitive ones.
- **Do NOT use** for internal updates (`stakeholder-update`) or feature release notes (`release-notes`).

## Inputs
- **Required:** the situation and the audience.
- **Optional:** the impact on customers, what you're offering/asking, brand voice, channel, legal constraints. *If missing, draft conservatively and flag where legal/leadership review is needed.*

## Method (methodology)
Customer-first clarity + appropriate ownership + trust calculus (sensitivity-scaled).
1. **Assess sensitivity** - routine announcement vs trust-affecting event (outage, price rise, data issue). Higher sensitivity = more care, more directness, less spin.
2. **Lead with impact + action** - what this means for them and what they need to do.
3. **Own it appropriately** - for problems: acknowledge, take proportional responsibility, no blame-shifting, no over-apologizing into liability.
4. **Be specific and honest** - timelines, what's fixed/not; never promise what you can't guarantee.
5. **Offer the path forward** - workaround, migration, compensation if warranted, where to get help.
6. **Tone** - calm, human, brand-consistent; avoid corporate fog and avoid alarming.
7. **Flag review** - note where legal/leadership must sign off (esp. data/security/financial).

## Output spec
A customer-ready message: impact + action up top · appropriate ownership · specific honest detail · path forward + support · matched tone. Plus a note flagging any legal/leadership review needed. Variants by channel on request.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the incident/change details; affected-customer segments; the migration/fix plan.
- **Outputs to Sprntly:** the comms logged against the event; follow-up commitments tracked.
- **Degrades to:** standalone; draft conservatively and flag review.

## Quality checklist (the bar)
- [ ] Leads with customer impact + action, not internal context.
- [ ] Ownership is proportional - neither blame-shifting nor over-apologizing.
- [ ] No promises that can't be guaranteed.
- [ ] Sensitive (data/security/financial) messages are flagged for review.

## Known gaps / limitations
- For data/security/legal/financial matters, this drafts but must not replace counsel/leadership review - it flags this explicitly.
- Brand voice varies; without it, defaults to clear-and-human.

## Worked example
**Input:** "3-hour export outage, now resolved, affected ~12% of accounts."
**Output (abridged):** Subject: "Export was down this morning - now fixed." Lead: "Between 9-12 ET, exporting was unavailable for some accounts. It's fully restored; no data was lost." Ownership: "This was on us - a deploy issue we've since rolled back." Specific: what failed, what's fixed, what we're doing to prevent recurrence (added export to our automated tests). Path: "If you still see issues, reply here." Tone: calm, no jargon. Flag: confirm "no data lost" with eng before sending.
