# Rubric and linters

Two layers. The **linters** are mechanical pass/fail checks — run them on every brief and treat failures as blocking. The **rubric** is the judgment layer — score each card and the greeting, and revise anything that fails a hard gate (SKILL.md step 6).

## Deterministic linters (blocking — fail = do not emit)

Run these as code where possible; they're cheap and catch most defects.

- **Grounding:** every number in a title/body/greeting maps to a field on the source signal (`pain.value`, `value.amount`/`range`, `reach.count`, or evidence). Any orphan number fails.
- **Totals integrity:** the greeting's "within reach" total equals the sum of the card value figures (within rounding). Mismatch fails.
- **Title shape:** the title contains a pain element and a value element (or an explicit qualitative value when `value.amount` is null). Pain-only fails.
- **Body length:** body ≤ 4 rendered lines at the template width (≈ 480 characters). Over fails.
- **Self-containment:** the body's first sentence names a concrete subject (not a bare pronoun like "It"/"This"). A leading bare pronoun fails.
- **Type ∈ taxonomy** and **accent == the type's hex.** Mismatch fails.
- **Valence:** loss-type cards (reliability, retention, competitive, compliance) do not use green; gain framing uses the gain color. Violation fails.
- **CTAs:** exactly two, labels from the allowed set, primary + ghost, in that order. PRD CTA label matches whether `prd_ref` exists (View vs Draft). Anything else fails.
- **No priority labels:** the tag is the category only — no "P0/P1". Presence fails.
- **No meta-widgets:** no "N signals agree" string and no confidence bar as a card element. Presence fails.
- **Source honesty:** number of chips == number of distinct `sources`; prose does not claim convergence when `sources.length == 1`.
- **Card count:** 1–7 cards. Outside fails.

## Rubric (scored — 0 / 1 / 2 per dimension; hard-gate dimensions must score 2)

| Dimension | Hard gate? | 2 (target) | 1 | 0 |
|---|---|---|---|---|
| Grounding | yes | every figure traced; ranges for projections | a figure with weak basis | an invented or false-precision figure |
| Self-containment | yes | body reads fully with the title removed | one mild dependency on the title | body is meaningless without the title |
| Title: pain + value | yes | both present, tight, value uses an action verb | both present but clunky/long | value missing |
| Valence/color | yes | accent matches type and valence | minor off-tone | gain color on a loss |
| CTA correctness | yes | exactly two, correct labels & order | minor label slip | wrong/missing/extra |
| Arc completeness | no | why → worth → review-and-approve all present | one beat thin | reads as a diagnosis with no path to action |
| Tone | no | PM voice; work-is-done posture; not preachy | slightly generic | salesy or assigns homework ("you should build…") |
| Greeting | no | 3 lines, offensive, totals correct, names top plays | 4 lines or flat framing | defensive or totals wrong |
| Prioritization | no | strongest-leverage card is first | defensible but not ideal order | clearly mis-ordered |
| Restraint | no | weak signals suppressed; honest if quiet | one borderline card surfaced | noise/manufactured urgency |

**Revise rule:** if any hard-gate dimension scores below 2, rewrite that card (or the greeting) once and re-score. If it still fails grounding, drop the figure or the card rather than ship an ungrounded claim.

## What to check against the goldens

After linting, compare voice and shape to `references/examples.md`:
- Do the titles read like the golden titles (pain stat, then value-of-acting)?
- Do the bodies follow the why → worth → review-and-approve arc and stand alone?
- Does the greeting frame upside, not defense?
- Did you avoid every numbered anti-pattern in the counter-examples section?
