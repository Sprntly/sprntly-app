---
name: interview-synthesis
description: Turn raw interview notes or transcripts into structured insights. Use when the user says "synthesize these interviews", "what are the themes", "analyze user research", or pastes notes/transcripts from customer conversations. Produces ranked themes, supporting evidence, JTBD signals, and decisions the research supports.
---

# Interview Synthesis

## What it does
Converts messy interview notes or transcripts into a structured synthesis: the recurring themes, how strong the signal is for each, representative (paraphrased) evidence, the jobs/pains surfaced, and what decision the research now supports. It distinguishes a pattern seen across many people from a single vivid anecdote.

## When to use / when NOT to use
- **Use** after interviews to find patterns and decide what to do.
- **Do NOT use** to design the interviews (`interview-guide`) or to build the opportunity map (`continuous-discovery`).

## Inputs
- **Required:** interview notes/transcripts (one or many).
- **Optional:** the learning goal, segment metadata per interviewee, number of interviews. *If missing, infer the apparent goal and state it; flag low-n.*

## Method (methodology)
Affinity mapping + signal-strength weighting + JTBD framing.
1. **Extract observations** — atomic, behavior-based statements per interview (paraphrase; never fabricate quotes).
2. **Cluster** into themes by affinity.
3. **Weight signal** — for each theme: how many interviewees, how consistent, how strong the emotion/behavioral evidence. Separate "many said" from "one said vividly."
4. **Frame jobs/pains** surfaced under each theme.
5. **Surface surprises and disconfirming evidence** explicitly (avoid confirmation bias).
6. **Translate to decisions** — what each theme implies for product/priorities, and what's still unknown.

## Output spec
Ranked themes, each with: signal strength (e.g., 7/9 interviewees), 1–3 paraphrased evidence points, the job/pain, and the implication. Plus a "surprises / disconfirming" section and "what we still don't know."

## Sprntly integration (optional)
- **Inputs from Sprntly:** transcripts and diarized calls from connected sources; the OST opportunity the research targets.
- **Outputs to Sprntly:** themes written back as opportunities/evidence on the OST; confidence updates to related findings in the outcome graph.
- **Degrades to:** with pasted notes only, synthesize directly and ask for n and goal if useful.

## Quality checklist (the bar)
- [ ] Themes carry an explicit signal strength, not just a list.
- [ ] Evidence is paraphrased, never invented.
- [ ] Disconfirming evidence and surprises are surfaced.
- [ ] Each theme ends in a decision/implication, not just a description.
- [ ] Low sample sizes are flagged, not hidden.

## Known gaps / limitations
- Garbage in, garbage out: thin or leading-question notes produce weak synthesis — it will flag this but can't fix the source data.
- It identifies patterns; causation still needs experiments.
- It does not replace the PM's judgment on which theme to act on.

## Worked example
**Input:** notes from 9 trial users on teammate invites.
**Output (abridged):** Theme 1 — "inviting feels premature before I've proven value myself" (7/9, strong). Evidence: paraphrased instances of waiting until after personal setup. Job: "look credible to my team before introducing a new tool." Implication: move the invite prompt to post-first-success, not onboarding. Surprise: 2/9 wanted to invite immediately for shared setup. Unknown: does delayed invite hurt activation? → experiment.
