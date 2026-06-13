---
name: product-strategy-stack
description: Check and build coherence from mission down through strategy, roadmap, and goals. Use when the user says "product strategy", "is our strategy coherent", "connect strategy to roadmap", "strategy stack", or "our roadmap doesn't match our strategy". Produces the aligned mission→vision→strategy→roadmap→goals stack and flags where the levels contradict each other.
---

# Product Strategy Stack

## What it does
Lays out the full strategy stack — mission, vision, strategy, roadmap, goals — and tests whether each level actually follows from the one above. Most "strategy" problems are really coherence problems: a roadmap that doesn't serve the strategy, or goals that reward the wrong thing. This skill finds and fixes those breaks.

## When to use / when NOT to use
- **Use** to author or audit product strategy and its alignment to execution.
- **Do NOT use** for the vision statement alone (`product-vision`), the roadmap alone (`roadmap`), or goal-setting alone (`okr-nct`).

## Inputs
- **Required:** current strategy materials OR the intent to build one (mission/market).
- **Optional:** existing roadmap, goals, market context. *If missing, build top-down from mission and label assumptions.*

## Method (methodology)
Ravi Mehta's product strategy stack + "good strategy/bad strategy" (Rumelt: diagnosis → guiding policy → coherent action).
1. **Mission** — why the company exists.
2. **Vision** — the future state you're driving toward.
3. **Strategy** — the diagnosis (what's really going on), guiding policy (the chosen approach), and coherent actions (what you'll do and won't). Reject "strategy" that's just goals or aspirations.
4. **Roadmap** — themes that execute the strategy (outcomes, not feature lists).
5. **Goals** — metrics that measure progress and reward the right behavior.
6. **Coherence audit** — walk top-down and bottom-up; flag every place a level doesn't serve the one above or rewards a contradiction.

## Output spec
The five-level stack, each populated; a coherence audit listing breaks (level, contradiction, fix); the 1–2 highest-leverage realignments.

## Sprntly integration (optional)
- **Inputs from Sprntly:** existing roadmap + goals + active initiatives from the knowledge graph to audit against the stated strategy.
- **Outputs to Sprntly:** the strategy stack as a reference entity; coherence breaks flagged against in-flight work.
- **Degrades to:** standalone from provided materials.

## Quality checklist (the bar)
- [ ] "Strategy" contains a real diagnosis + guiding policy, not just goals.
- [ ] The roadmap is themes/outcomes, not a feature dump.
- [ ] Goals don't reward behavior that contradicts the strategy.
- [ ] Coherence breaks are named with concrete fixes.

## Known gaps / limitations
- Can't supply market truth — a coherent stack built on a wrong diagnosis is still wrong; pair with `market-structure`/`competitive-intelligence-review`.
- Strategy quality ultimately needs leadership judgment; this enforces structure and coherence, not correctness of the bet.

## Worked example
**Input:** "Mission: help SMBs run on AI. Roadmap is mostly enterprise SSO + audit logs."
**Output (abridged):** Coherence break: roadmap serves enterprise buyers, strategy targets SMB self-serve — the levels contradict. Likely cause: chasing a few large logos. Fix: either restate strategy to include up-market motion (and resource it), or move SSO/audit to "later" and refocus the roadmap on SMB activation. Don't pretend both are the priority.
