---
name: fact-check
description: Verify that the claims in an artifact are actually grounded in real sources — catch, flag, and remove anything made up. Use when the user says "fact-check this", "check for made-up info", "verify these claims", "is this hallucinated", "make sure nothing is fabricated", "where did this come from", or as a verification pass over any research/enrichment output (business context, competitive review, PRD evidence, a report). Produces a per-claim verdict (supported / overstated / unsupported / contradicted) with the exact supporting source snippet, plus a list of unsupported claims to delete or confirm and a groundedness score.
---

# Fact-Check (groundedness verification)

## What it does
Takes an artifact and checks every factual claim in it against real evidence, so anything **made up** is caught instead of trusted. For each claim it finds the actual supporting source and snippet, or it flags the claim as unsupported. The point isn't to judge whether a claim is true in the world — it's to verify the claim is **grounded**: traceable to something the user provided, a document that was actually fetched, or a shown derivation — and never silently sourced from model memory or invented. It turns "trust me, I didn't fabricate" into a checkable, per-claim audit.

This is a **verification pass**, designed to run *over* the output of another skill (or any document), and it composes cleanly into enrichment/research skills that must not hallucinate.

## When to use / when NOT to use
- **Use** to verify any artifact where fabrication is costly: a business context, a competitive review, a PRD's evidence/claims, a research summary, a customer-facing brief — anywhere a confident-but-wrong "fact" would do damage.
- **Use** as the final pass inside another skill (e.g. `business-context`, `competitive-intelligence-review`) before delivery.
- **Do NOT use** to check opinions, recommendations, or creative content (those aren't factual claims), to verify code correctness (that's testing), or to establish ground-truth about the world where no source exists (it checks *groundedness in available sources*, not absolute truth — say so when sources are absent).

## Inputs
- **Required:** the artifact to check.
- **Optional but strongly recommended:** the sources it was supposedly built from (URLs, fetched docs, the user's own inputs). *If sources are provided, the skill binds each claim to one. If sources are NOT provided, the skill can re-fetch cited URLs to verify, and for anything with no citation at all it must treat the claim as unverified — it cannot confirm a claim by reasoning alone.*

## Method (methodology)
Claim-by-claim groundedness audit (the technique behind hallucination/faithfulness evaluation), applied as a practical verification pass.
1. **Extract atomic claims.** Break the artifact into individual factual assertions — each number, name, date, relationship, and stated fact is its own claim. (Opinions, recommendations, and clearly-hedged statements are set aside, not checked.)
2. **Classify each claim's origin:** *given* (the user said it) · *sourced* (in a fetched document) · *derived* (reasoned from sourced facts, with the steps shown) · *unattributed* (no traceable origin — the prime suspect for fabrication).
3. **Bind evidence.** For every claim, locate the specific supporting source **and quote the exact snippet** that backs it. If no snippet can be found, the claim does not get to stay as a fact.
4. **Assign a verdict per claim:**
   - **Supported** — a source snippet directly backs it (cite it).
   - **Overstated** — a source partly backs it but the claim says more/stronger than the source (soften to what's supported).
   - **Unsupported** — no source backs it; likely fabricated → **flag for removal or confirmation.**
   - **Contradicted** — a source says otherwise → **fix to match the source.**
5. **Numbers get extra scrutiny.** Any figure (revenue, traffic, share, count, %, price) must appear verbatim in a source or be a shown calculation from sourced inputs. A number that "feels right" but can't be traced is Unsupported by definition — this is where fabrication hides most often.
6. **Triangulate high-stakes claims.** For load-bearing facts (a revenue model, a market-share number, a safety/legal claim), prefer two independent sources; flag single-weak-source claims as low-confidence even if technically "sourced."
7. **Catch the tells of made-up content:** suspiciously round or precise numbers with no citation; named people/companies/products with no source; specific dates with no source; claims that restate the prompt's assumptions as findings; and anything attributable only to "general knowledge" about a fast-changing or specific entity. Each of these → Unsupported until a source is bound.
8. **Score and report.** Output the per-claim audit, the flagged list, and a **groundedness score** (supported claims ÷ total factual claims), with the recommended fix for each non-supported claim.

## Output spec
A verification report (`templates/factcheck-log.md`):
1. **Verdict summary** — groundedness score (e.g. "37 of 42 claims supported"), and counts by verdict.
2. **Flagged claims (the important part)** — every Unsupported / Contradicted / Overstated claim, what's wrong, and the fix (remove / confirm / soften / correct). These are the "made-up or shaky" items.
3. **Evidence log** — for supported claims, the claim → source → exact snippet binding, so a human or another agent can re-verify independently.
4. **A cleaned artifact** (optional, if asked) — the input with unsupported claims removed/softened and corrections applied.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the source documents/connectors the artifact was built from, so claims bind to first-party evidence rather than re-fetched guesses; prior verified facts from the knowledge graph.
- **Outputs to Sprntly:** the groundedness score and flagged list recorded against the artifact; verified claims promoted with their evidence; unsupported claims blocked from being written to the knowledge graph (so fabrication can't propagate to downstream agents).
- **Degrades to:** standalone — runs on the artifact + whatever sources are provided or re-fetchable; with no sources it honestly reports "cannot verify" rather than rubber-stamping.

## Quality checklist (the bar)
- [ ] Every factual claim (esp. every number, name, and date) got a verdict — none waved through.
- [ ] Supported claims carry an **exact source snippet**, not a vague "per the site."
- [ ] Unsupported claims are **flagged for removal/confirmation**, not quietly kept.
- [ ] Numbers were held to the verbatim-or-shown-derivation bar.
- [ ] The report distinguishes "grounded in the provided sources" from "true in the world" — it never claims to verify absolute truth when it only checked sources.
- [ ] A groundedness score is given, and the fix for each flagged claim is stated.

## Known gaps / limitations
- It verifies **groundedness against available sources**, not absolute truth — a claim faithfully sourced from a wrong source still passes, so source quality matters (note dubious sources).
- With no sources provided and no citations to re-fetch, it can only mark claims unverified — it cannot bless a claim by reasoning.
- It checks factual claims, not the soundness of inferences or recommendations (pair with `red-team-review` for those).
- A determined author can still cite a real-but-irrelevant source; the snippet-binding requirement makes that visible but not impossible — spot-check high-stakes items.

## Worked example
**Input:** a business-context brief stating "ChannelMeter processes $1B+ in payments across 50+ countries" and "ChannelMeter has ~120 employees and raised a $20M Series A," with the company website provided as the source.
**Output (abridged):**
- **Claim:** "$1B+ payments, 50+ countries, 21+ currencies" → **Supported.** Snippet from channelmeter.com: "$1B+ payments processed … 50+ countries … 21+ currencies." Cite + keep.
- **Claim:** "~120 employees" → **Unsupported.** No employee count appears on the site or any provided source → flag: remove, or confirm from a real source (LinkedIn/filing).
- **Claim:** "$20M Series A" → **Unsupported / likely fabricated.** No funding figure in any provided source; a specific number with no citation is the classic tell → flag for removal.
- **Groundedness score:** 1 of 3 factual claims supported. **Recommended fixes:** delete the employee count and funding claims (or source them); keep the payments stats with their snippet.
