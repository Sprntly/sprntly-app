---
name: problem-framing
description: Turn a vague request or feature ask into a sharp, solution-free problem statement. Use when the user says "frame this problem", "what's the real problem", "this came in as a feature request", or hands over an ambiguous ask. Produces a problem statement naming the user, job, pain, and evidence — with the solution stripped out.
---

# Problem Framing

## What it does
Takes an incoming request — often phrased as a solution ("build X") — and reframes it as a real problem worth solving: who has it, the job they're trying to do, the pain, the impact, and the evidence. It is the gate that stops solution-first work.

## When to use / when NOT to use
- **Use** at the start of any initiative, especially when work arrives as a feature request.
- **Do NOT use** to write the full PRD (`prd-author`) or build the opportunity map (`continuous-discovery`).

## Inputs
- **Required:** the request/ask in any form.
- **Optional:** who asked, supporting data, the metric it relates to. *If missing, reframe with explicit `[ASSUMPTION]` tags and list what to confirm.*

## Method (methodology)
"Five whys" + problem-statement discipline (Cagan: problem before solution) + impact sizing.
1. **Detect the smuggled solution.** If the ask names a feature, ask what problem it's meant to solve.
2. **Whys.** Trace from the surface ask to the underlying need (2–5 whys).
3. **State the problem:** "[User] is trying to [job] but [pain], which causes [impact]."
4. **Evidence & size.** What signal exists; how many users; how costly. Label gaps.
5. **Reframes.** Offer 1–2 alternative framings (narrower/broader) so the team picks the right altitude.
6. **Success shape.** What measurable change would mean the problem is solved (hand-off to metric design).

## Output spec
The smuggled-solution flag (if any), the why-chain, a one-sentence problem statement, evidence + rough size, 1–2 alternative framings, and the success shape.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the raw request as a finding; supporting signals + confidence to populate evidence/size.
- **Outputs to Sprntly:** the framed problem as a backlog/opportunity item feeding `prd-author` and `continuous-discovery`.
- **Degrades to:** with no context, reframe from the text and ask only the highest-leverage clarifying question.

## Quality checklist (the bar)
- [ ] Final statement contains no solution.
- [ ] The why-chain reaches a genuine need, not a restated symptom.
- [ ] Evidence and rough size are present (or explicitly assumed).
- [ ] At least one alternative framing is offered.

## Known gaps / limitations
- Can over-abstract; the alternative-framings step guards against framing the problem too broadly to act on.
- Sizing needs data; with none it estimates and labels.

## Worked example
**Input:** "Sales wants a CSV export button."
**Output (abridged):** Smuggled solution: "export button." Whys → reps manually rekey data into their own trackers → they don't trust our reporting view → the report lacks the 3 fields they care about. Problem: "Reps trying to report pipeline to leadership can't get the 3 fields they need in our view, so they rebuild reports in spreadsheets, costing ~4 hrs/week each." Alt framings: (a) add the 3 fields to the report; (b) export. Success: rep-built spreadsheets drop to near zero.
