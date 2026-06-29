---
name: proofread-polish
description: Check a PM document for grammar, clarity, logic, and flow — and fix it — without changing the meaning or flattening the voice. Use when the user says "proofread this", "grammar check", "tighten this up", "fix the writing", "polish this", or wants a doc cleaned before it goes out. Fixes errors and weak phrasing, flags logic gaps, preserves intent and tone; shows what changed so the author stays in control.
---

# Proofread & Polish

## What it does
Cleans a document — grammar, spelling, clarity, logic, flow — and returns a tightened version that keeps the author's meaning and voice. It fixes mechanical errors, sharpens weak/wordy phrasing, and flags places where the *logic* (not just the grammar) doesn't hold — while showing what changed so the author keeps control.

## When to use / when NOT to use
- **Use** to polish a PRD, update, post, or email before it ships.
- **Do NOT use** to write from scratch, or to restructure a doc's argument (that's `exec-narrative` / the authoring skills).

## Inputs
- **Required:** the text.
- **Optional:** audience, desired tone, length target, house style. *Won't invent facts to fill a gap — flags it instead.*

## Method (methodology)
1. **Mechanics** — grammar, spelling, punctuation, consistency.
2. **Clarity & concision** — cut filler, fix ambiguous pronouns, tighten without changing meaning.
3. **Logic & flow** — flag claims that don't follow, missing transitions, contradictions (flag, don't silently rewrite the argument).
4. **Preserve voice** — keep the author's tone and intent; polish, don't homogenize.
5. **Show changes** — summary of what changed (and any logic flags) so the author decides.

## Output spec
The polished text + a short "what changed" summary (mechanics fixed, phrasing tightened, logic flags raised). On request, inline tracked-style annotations.

## Quality checklist (the bar)
- [ ] Mechanics fixed; concision improved.
- [ ] Meaning unchanged; voice/tone preserved (not flattened).
- [ ] Logic gaps/contradictions flagged, not silently rewritten.
- [ ] Changes shown so the author stays in control; no invented facts.

## Known gaps / limitations
- Polishes prose; it doesn't fix a weak underlying argument (use an authoring/critique skill).
- Style is partly taste — it proposes, the author disposes.

## Worked example
**Input:** a wordy product announcement. Output: tightened copy (−30% length), 2 grammar fixes, 1 flagged logic gap ("claims X but the metric shows Y"), voice intact, change summary attached.
