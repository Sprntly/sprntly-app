---
name: persona-segment
description: Define actionable, evidence-based personas or behavioral segments. Use when the user says "create a persona", "define our segments", "who is our user", or "behavioral segmentation". Produces personas grounded in behavior and jobs (not demographics alone), each with a confidence level and the evidence behind it.
---

# Persona / Segment

## What it does
Produces personas or segments defined by behavior, goals, and jobs — not just age/title/industry — so they actually inform product decisions. Each persona carries an evidence basis and a confidence label, preventing the "marketing fiction" failure mode.

## When to use / when NOT to use
- **Use** to align a team on who they're building for, or to segment by behavior for targeting.
- **Do NOT use** to frame a single problem (`problem-framing`) or define the job (`jobs-to-be-done`).

## Inputs
- **Required:** the product and any sense of the user base.
- **Optional:** research, usage/behavioral data, sales/support input. *If missing, produce provisional personas explicitly marked low-confidence and list what evidence would confirm them.*

## Method (methodology)
Behavioral segmentation + jobs-based personas + evidence/confidence labeling (product-on-purpose persona pattern).
1. **Choose the axis** — segment by behavior, job, or need, not demographics (demographics are descriptors, not drivers).
2. **Draft each persona:** goals, the job they're hiring the product for, key behaviors, pains, decision triggers, and what success looks like for them.
3. **Anti-persona** — explicitly who you're *not* building for.
4. **Evidence & confidence** — tag each persona High/Med/Low based on the data behind it.
5. **Make it actionable** — for each persona, the one product implication that follows.

## Output spec
2–4 personas (behavioral), each with goals, job, behaviors, pains, triggers, evidence + confidence, and a product implication. Plus an anti-persona.

## Sprntly integration (optional)
- **Inputs from Sprntly:** behavioral cohorts from product analytics, support/sales signals, knowledge-graph entities.
- **Outputs to Sprntly:** personas as first-class entities in the knowledge graph, referenced by `prd-author`, `user-stories`, `positioning`.
- **Degrades to:** with no data, produce provisional, clearly-labeled personas.

## Quality checklist (the bar)
- [ ] Personas are distinguished by behavior/jobs, not demographics alone.
- [ ] Each carries an evidence basis and confidence level.
- [ ] An anti-persona is included.
- [ ] Each persona yields a concrete product implication.

## Known gaps / limitations
- Personas drift from reality over time; they need refresh against data.
- Low-confidence personas are hypotheses; treating them as fact is the main risk — hence mandatory confidence tags.

## Worked example
**Input:** "B2B scheduling tool, mixed user base."
**Output (abridged):** Persona A "Coordinating Operator" (High) — job: keep a multi-person calendar conflict-free; behavior: logs in daily, heavy bulk edits; pain: double-bookings; implication: bulk conflict detection. Anti-persona: solo freelancer with one calendar (we won't optimize for them).
