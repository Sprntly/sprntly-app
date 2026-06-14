---
name: interview-guide
description: Design a non-leading customer interview guide for a specific learning goal. Use when the user says "write interview questions", "discovery interview guide", "customer interview script", or "what should I ask users about X". Produces an unbiased guide built around past behavior, not hypotheticals or feature-pitching.
---

# Interview Guide

## What it does
Produces a customer interview guide aimed at a clear learning goal, engineered to avoid the classic traps: leading questions, hypotheticals, and pitching the solution. It anchors on real past behavior ("tell me about the last time…") so the data is about what people actually did, not what they say they'd do.

## When to use / when NOT to use
- **Use** to prepare for discovery, problem-validation, or JTBD interviews.
- **Do NOT use** to synthesize results after interviews (`interview-synthesis`) or design surveys (`survey-design`).

## Inputs
- **Required:** the learning goal / decision the interviews should inform.
- **Optional:** target segment, hypotheses being tested, time per interview. *If missing, ask only for the learning goal; default to a 30-minute structure.*

## Method (methodology)
The Mom Test (Rob Fitzpatrick) + Teresa Torres story-based interviewing + JTBD timeline.
1. **State the learning goal** and what decision it informs.
2. **Warm-up** — context about the person and their role/workflow.
3. **Story prompts** — "Tell me about the last time you [did the relevant job]." Mine the specific instance, not generalities.
4. **Dig** — follow the timeline: what triggered it, what they tried, where it broke, workarounds, emotion.
5. **Avoid** — no "would you," no "do you like the idea of," no pitching. Flag any leading question and rewrite it.
6. **Wrap** — referrals, anything-else, permission to follow up.
7. Mark which questions map to which hypothesis/assumption.

## Output spec
A guide with: learning goal · warm-up · 3–6 story-based core questions with follow-up probes · explicit "avoid saying" list · wrap-up · a map of question → hypothesis.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the opportunity/assumption under test from the OST or Monday Brief; segment from the knowledge graph.
- **Outputs to Sprntly:** the guide as an artifact linked to the opportunity; placeholders for results to flow into `interview-synthesis`.
- **Degrades to:** with no context, ask for the learning goal and produce a standalone guide.

## Quality checklist (the bar)
- [ ] Core questions ask about specific past behavior, not hypotheticals.
- [ ] Zero leading or solution-pitching questions.
- [ ] Each question ties to the learning goal/a hypothesis.
- [ ] Follow-up probes are included, not just top-level questions.

## Known gaps / limitations
- A guide doesn't guarantee good interviewing; note technique tips but it can't run the session.
- For sensitive topics, add ethics/consent guidance the skill won't infer.

## Worked example
**Input:** "Learning goal: why do trial users not invite teammates?"
**Output (abridged):** Story prompt: "Walk me through the last time you set up a new tool for your team — what did you do first?" Probes: who else was involved, when did you bring them in, what made you wait. Avoid: "Would you invite teammates if it were easier?" (hypothetical + leading). Map: Q3 → assumption that inviting feels premature during trial.
