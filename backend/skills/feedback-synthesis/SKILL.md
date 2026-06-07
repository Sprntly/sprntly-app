---
name: feedback-synthesis
description: Synthesize scattered product feedback into prioritized themes. Use when the user says "synthesize feedback", "what are users asking for", "go through these feature requests", "support ticket themes", "review feedback", or pastes a pile of feedback. Produces clustered themes with frequency and signal strength, the underlying need behind each, and a recommended response - distinct from interview synthesis.
---

# Feedback Synthesis

## What it does
Takes a pile of unstructured feedback - support tickets, feature requests, reviews, sales notes, NPS comments - and turns it into prioritized themes with frequency, the *underlying need* behind each surface request, and a recommended response (build / already exists / won't do / needs discovery). It separates loud-but-rare from quiet-but-common and reads the need beneath the asked-for feature.

## When to use / when NOT to use
- **Use** for ongoing/aggregated feedback across channels at scale.
- **Do NOT use** for structured research interviews (`interview-synthesis`) or formal win/loss deal analysis.

## Inputs
- **Required:** the feedback corpus (paste/export).
- **Optional:** source per item, customer segment/value, volume context. *If missing, synthesize structurally and note that frequency lacks denominator context.*

## Method (methodology)
Theme clustering + signal weighting + need-behind-the-request + response routing.
1. **Normalize** items; capture source + (if available) who said it.
2. **Cluster** into themes; collapse the same need expressed as different feature asks.
3. **Weight** - frequency AND segment value (10 enterprise asks may outweigh 100 free-tier asks, depending on strategy); separate loud-rare from quiet-common.
4. **Read the need** - the job behind the requested feature (users ask for faster horses).
5. **Route each theme** - already addressed / build candidate (-> `prioritize`) / needs discovery (-> `continuous-discovery`) / won't do (with reason).
6. **Surface the surprising signal** - the theme leadership isn't expecting.

## Output spec
Ranked themes, each with: frequency + segment weighting · the underlying need (not just the feature asked) · representative paraphrased examples · a recommended response/route · plus a "surprising signal" callout.

## Sprntly integration (optional)
- **Inputs from Sprntly:** feedback aggregated across connected sources (support, reviews, sales) with segment + value metadata.
- **Outputs to Sprntly:** themes as opportunities with frequency/value; routes wired to `prioritize` or discovery; recurring themes tracked over time.
- **Degrades to:** standalone from pasted feedback; flag missing denominator.

## Quality checklist (the bar)
- [ ] Themes weight segment value, not just raw count.
- [ ] The underlying need is read, not just the literal feature request.
- [ ] Loud-rare is distinguished from quiet-common.
- [ ] Each theme is routed (build/exists/discovery/won't), not just listed.

## Known gaps / limitations
- Feedback is self-selected (vocal minority); frequency lacks a denominator without volume context - flagged.
- The need-behind-the-request is interpretive; mark inference vs explicit ask.

## Worked example
**Input:** "200 mixed tickets + requests."
**Output (abridged):** Theme 1 - "export is too slow/limited" (45 mentions, spans free + 6 enterprise = high weighted): need = trust our reporting enough to use it live. Route: build candidate -> `prioritize`. Theme 2 - "want dark mode" (60 mentions, mostly free): loud but low strategic value -> backlog, not now. Surprising signal: 8 enterprise tickets hint at an audit-trail need no one's tracking -> needs discovery.
