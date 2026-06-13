---
name: release-notes
description: Write audience-appropriate release notes. Use when the user says "write release notes", "changelog", "what's new", "announce this update", or has shipped something to communicate. Produces release notes pitched to the right audience - user-facing benefit language, not internal commit messages.
---

# Release Notes

## What it does
Turns a set of shipped changes into release notes written for the reader - leading with the benefit and the job it helps, not the internal feature name or commit log. It can produce variants for different audiences (end users, admins, developers) from the same changes.

## When to use / when NOT to use
- **Use** to communicate what shipped, in a changelog or in-product "what's new."
- **Do NOT use** for a full launch plan (`launch-gtm`) or customer announcement campaigns (`customer-comms`).

## Inputs
- **Required:** what shipped (changes/features).
- **Optional:** audience, tone/brand, links to docs, whether it's user-visible vs under-the-hood. *If missing, default to end-user audience and benefit-led tone.*

## Method (methodology)
Benefit-led communication + audience tailoring + scannability.
1. **Group changes** by what the reader cares about (features, improvements, fixes).
2. **Lead with benefit** - "Do X faster" not "Added endpoint Y."
3. **Tailor to audience** - end users get benefits, developers get API specifics, admins get config impact.
4. **Be honest about fixes** without alarming (don't over-detail security fixes).
5. **Scannable format** - headline + one line each; link to depth.
6. **Call out anything requiring action** (migrations, deprecations).

## Output spec
Grouped notes (New / Improved / Fixed) with benefit-led one-liners · audience-appropriate framing · any required-action callouts · optional per-audience variants.

## Sprntly integration (optional)
- **Inputs from Sprntly:** shipped items from the Shipped & Outcomes view; the PRDs behind them for benefit language.
- **Outputs to Sprntly:** release notes as an artifact tied to the shipped items; feeds `customer-comms` if a campaign is needed.
- **Degrades to:** standalone from the change list.

## Quality checklist (the bar)
- [ ] Each note leads with user benefit, not internal naming.
- [ ] Framing matches the stated audience.
- [ ] Required actions (migrations/deprecations) are called out.
- [ ] Scannable - no wall of text.

## Known gaps / limitations
- Can't infer user benefit from a cryptic commit; vague input yields vague notes - it will ask what the change does for the user.
- Security-fix phrasing should be reviewed by the team; the skill defaults to non-alarming, non-detailed.

## Worked example
**Input:** "Shipped: caching layer, new bulk-edit, fixed CSV export crash."
**Output (abridged):** New - Bulk edit: change many records at once instead of one by one. Improved - Faster load times across the app. Fixed - CSV export no longer crashes on large files. (Developer variant would name the cache + export fix specifics.)
