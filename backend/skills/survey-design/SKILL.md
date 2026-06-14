---
name: survey-design
description: Design or critique a quantitative survey and its analysis plan. Use when the user says "write a survey", "survey questions", "design a questionnaire", or "how should I analyze this survey". Produces unbiased questions, the right scales, a sampling/representativeness plan, and an analysis approach — flagging common bias traps.
---

# Survey Design

## What it does
Designs a survey that produces trustworthy quantitative signal: clear unbiased questions, appropriate scales, a sampling plan that addresses representativeness and bias, and an analysis plan defined *before* data collection. Or critiques an existing survey for the same.

## When to use / when NOT to use
- **Use** for quantitative measurement at scale (satisfaction, willingness-to-pay screens, feature demand, NPS-style tracking).
- **Do NOT use** for qualitative depth (`interview-guide`) — surveys can't answer "why" well.

## Inputs
- **Required:** the question the survey must answer.
- **Optional:** audience, sample size available, distribution channel, prior survey. *If missing, ask for the core question; default to good-practice scales.*

## Method (methodology)
Survey methodology fundamentals: construct validity, question hygiene, sampling.
1. **Define the decision** the survey informs and the constructs to measure.
2. **Write items** — one idea per question, no double-barrels, no leading wording, balanced scales; mix in attention checks.
3. **Choose scales** appropriate to each construct (Likert, ranking, MaxDiff for tradeoffs, etc.).
4. **Sampling plan** — who, how many (rough power/precision), how to reduce self-selection/non-response bias.
5. **Analysis plan up front** — what cut/test answers the decision; pre-register the primary metric.
6. **Bias audit** — flag every leading/loaded/ambiguous item and rewrite.

## Output spec
Survey purpose + decision · ordered items with scales · attention/quality checks · sampling & representativeness plan · pre-specified analysis plan · bias-audit notes.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the hypothesis/opportunity to quantify; audience cohorts from analytics.
- **Outputs to Sprntly:** the survey artifact; the pre-specified metric registered to the outcome graph so results can update confidence.
- **Degrades to:** standalone with the core question only.

## Quality checklist (the bar)
- [ ] No double-barreled, leading, or loaded items.
- [ ] Scales fit the constructs; tradeoff questions use ranking/MaxDiff not flat ratings.
- [ ] Sampling plan addresses representativeness and non-response.
- [ ] Analysis plan is defined before collection (no fishing).

## Known gaps / limitations
- Surveys measure stated, not revealed, preference — willingness-to-pay especially is overstated; note this.
- Without sample-size context it can't guarantee power; it gives rough guidance only.

## Worked example
**Input:** "Survey to size demand for a mobile app."
**Output (abridged):** Decision: build native mobile or responsive web. Items: behavior frequency on mobile (not "would you like an app?"), task-importance ranking, current mobile workaround. Avoids the leading "Do you want a mobile app?" Sampling: stratify by usage tier; n≈300 for ±6% precision. Analysis: pre-specify % who do the top job on mobile weekly as the go/no-go metric.
