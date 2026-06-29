---
name: naming-brainstorm
description: Brainstorm product / feature / company names aligned to brand and audience, screened for the practical traps — pronounceability, meaning, and obvious conflicts — before you fall in love with one. Use when the user says "name ideas", "what should we call this", "naming", "product name", "rename X". Generates varied directions (descriptive → evocative → abstract), screens each for red flags, and recommends a shortlist with rationale — not 50 undifferentiated options.
---

# Naming Brainstorm

## What it does
Generates name candidates across deliberate directions (descriptive, evocative, abstract, invented), each tied to the brand values and audience, then **screens for the practical traps** — hard to say/spell, unintended meanings, obvious existing-name collisions — and recommends a shortlist with rationale. The goal is a usable short list with reasons, not a wall of options.

## When to use / when NOT to use
- **Use** to generate and pre-screen names for a product, feature, or company.
- **Do NOT use** for positioning/messaging (`positioning`) or final legal/trademark clearance (always a lawyer's job — see limits).

## Inputs
- **Required:** what's being named + a sense of brand values / audience / tone.
- **Optional:** names to avoid, competitor names, desired vibe, domain/handle needs.

## Method (methodology)
1. **Anchor** on brand values, audience, and tone (one line).
2. **Generate across directions** — descriptive (says what it does), evocative (feels like the benefit), abstract/invented (ownable) — several each, so options are genuinely different.
3. **Screen each** for red flags: pronounceability, spelling, unintended/negative meanings (incl. other languages where relevant), and obvious collisions with known products.
4. **Shortlist 3-5** with rationale + the trade-off of each; note what to verify.

## Output spec
Candidates grouped by direction, a red-flag screen per finalist, then a recommended shortlist (3-5) with rationale and a "verify before committing" note.

## Sprntly integration (optional)
- **Inputs:** brand values from `business-context`; positioning from `positioning`.
- **Outputs:** the shortlist; chosen name → `positioning` / `launch-gtm`.
- **Degrades to:** standalone from a one-line brief.

## Quality checklist (the bar)
- [ ] Options span real directions (not 20 variants of one idea).
- [ ] Each finalist screened for pronounceability/meaning/obvious collisions.
- [ ] A reasoned shortlist of 3-5, not an undifferentiated dump.
- [ ] Flags that trademark/domain checks are still required.

## Known gaps / limitations
- **Not trademark or domain clearance** — it flags obvious collisions, but legal availability needs a real trademark search + a lawyer.
- Name resonance is partly subjective; test the shortlist with the actual audience.

## Worked example
**Input:** name a new analytics view in Sprntly, audience PMs, tone "sharp, trustworthy." Directions generated; finalists screened; shortlist of 3 with rationale + note to check existing-feature collisions and trademark.
