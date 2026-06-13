---
name: pre-mortem
description: Run a pre-mortem to surface failure modes before committing. Use when the user says "pre-mortem", "how could this fail", "what could go wrong", "stress test this plan", or before a big launch/bet. Produces ranked failure modes with early-warning signals and mitigations, imagining the project has already failed.
---

# Pre-Mortem

## What it does
Imagines the initiative has already failed in 6–12 months and works backward to the most likely causes, then ranks them, attaches early-warning signals, and assigns mitigations. It legitimizes pessimism before commitment — catching the failure modes optimism hides.

## When to use / when NOT to use
- **Use** before a launch, a major bet, or committing significant resources.
- **Do NOT use** as a post-incident review (`retrospective`/`incident-runbook`) or to make the go/no-go itself (`decision-memo`).

## Inputs
- **Required:** the plan/initiative.
- **Optional:** team, timeline, dependencies, prior similar efforts. *If missing, run on the plan as stated and note where context would sharpen it.*

## Method (methodology)
Gary Klein's pre-mortem technique + failure-mode ranking by likelihood × impact + leading indicators. Failure-mode categories are the standard product/launch risk classes (demand, execution, adoption, organizational, external) — generic risk taxonomy, not a proprietary list.
1. **Frame the failure** — "It's [date]. This failed. Why?"
2. **Generate causes** broadly: demand (no one wanted it), execution (built wrong/late), adoption (shipped but unused), org (lost support), external (market/competitor).
3. **Rank** by likelihood × impact.
4. **Early-warning signal** per top cause — the metric/observation that would show it happening *before* it's fatal.
5. **Mitigation + owner** per top cause; note which mitigations are cheap insurance vs costly.
6. **Decide** what to change in the plan now.

## Output spec
The failure frame · ranked failure modes (likelihood×impact) · early-warning signal per top mode · mitigation + owner · plan changes to make now.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the plan/PRD; historical outcomes of similar bets from the outcome graph (which failure modes actually recur here).
- **Outputs to Sprntly:** early-warning signals registered as monitored metrics; mitigations written to the backlog with owners.
- **Degrades to:** standalone from the plan.

## Quality checklist (the bar)
- [ ] Causes span demand/execution/adoption/org/external — not just execution.
- [ ] Top modes have a *leading* indicator, not just a lagging one.
- [ ] Each top mode has an owned mitigation.
- [ ] The plan is actually changed as a result.

## Known gaps / limitations
- Can become a worry-list without prioritization — the likelihood×impact ranking forces focus.
- It surfaces risks; it can't eliminate them.

## Worked example
**Input:** "Launching a freemium tier."
**Output (abridged):** Top mode (adoption): free users never convert (high×high). Early signal: week-2 feature-limit hit rate < 20% (they're not bumping the wall). Mitigation: instrument the paywall moment, design the limit to bite at the value moment; owner: PM. Mode (viability): free support cost swamps margin. Signal: tickets/free-user > X. Mitigation: deflect via docs, cap free seats.
