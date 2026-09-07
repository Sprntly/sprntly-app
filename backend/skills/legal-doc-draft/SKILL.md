---
name: legal-doc-draft
description: Draft a first-pass NDA or privacy policy from your inputs — structured, plain-language, with the standard clauses and obvious gaps flagged — as a STARTING POINT for counsel, never as legal advice. Use when the user says "draft an NDA", "mutual NDA", "privacy policy", "GDPR/CCPA policy", "confidentiality agreement". Produces a reviewable draft with placeholders for the facts only you know, flags jurisdiction-sensitive clauses, and states clearly that a lawyer must review before use.
---

# Legal Doc Draft (NDA / privacy policy)

## What it does
Produces a clean, structured first draft of a common business legal document — a (mutual or one-way) **NDA** or a **privacy policy** — with the standard clauses, plain-language explanations, and bracketed placeholders for the specifics only you can supply. It accelerates getting to a reviewable draft; it does **not** replace a lawyer.

## When to use / when NOT to use
- **Use** to get a structured starting draft to then take to counsel.
- **Do NOT use** as final legal advice, for high-stakes/complex agreements, litigation, or anything regulated beyond a standard template (those need a lawyer from the start).

## Inputs
- **Required:** doc type (NDA one-way/mutual, or privacy policy) + the basics (parties / company + what data you collect).
- **Optional:** jurisdiction, term, specific carve-outs, the regulations in scope (GDPR/CCPA). *Unknown specifics become clearly-marked `[BRACKETED PLACEHOLDERS]`, never invented terms.*

## Method (methodology)
1. **Confirm type + key facts**; mark everything unknown as a placeholder.
2. **Assemble standard clauses** — NDA: definition of confidential info, obligations, exclusions, term, return/destruction, remedies. Privacy: data collected, purposes, legal basis, sharing, retention, user rights, contact.
3. **Flag jurisdiction-sensitive / fill-in clauses** the user (and their lawyer) must decide.
4. **Plain-language note** beside dense clauses so the user understands what they're agreeing to.
5. **State the limit prominently** (below).

## Output spec
The draft with placeholders, jurisdiction-sensitive clauses flagged, plain-language notes, and a prominent **"not legal advice — have a qualified attorney review before use"** banner at the top and bottom.

## Safety / limits (enforced)
- **This is not legal advice and does not create an attorney-client relationship.** Every output carries that notice. It's a drafting accelerator; a qualified lawyer in the relevant jurisdiction must review before anything is signed or published.
- Won't invent statutory citations or jurisdiction-specific guarantees; flags where local law governs.

## Sprntly integration (optional)
- **Inputs:** company facts from `business-context`.
- **Outputs:** a draft to route to counsel.
- **Degrades to:** standalone from doc type + basics.

## Quality checklist (the bar)
- [ ] Standard clauses present; unknowns are bracketed placeholders, not invented terms.
- [ ] Jurisdiction-sensitive clauses flagged for a lawyer.
- [ ] Plain-language notes on dense clauses.
- [ ] **"Not legal advice — lawyer must review" notice present, top and bottom.**

## Known gaps / limitations
- A template is not tailored counsel; law varies by jurisdiction and changes over time.
- Does not cover complex/regulated agreements — escalate those to a lawyer directly.

## Worked example
**Input:** "mutual NDA for a design-partner conversation." → a mutual NDA draft with bracketed parties/term/jurisdiction, standard clauses, plain-language notes, and the not-legal-advice banner — ready to hand to counsel.
