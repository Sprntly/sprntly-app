# State spec — `state/ci-state.json`

Extracted from SKILL.md ("Two modes → State between runs" and "Data integrity"). This is the contract for the state a run reads, diffs against, and rewrites.

Without stored state, "what changed" is a memory exercise, and memory is where fabrication enters. A run that cannot read prior state is a **baseline** run: populate the state and omit every diff section rather than inventing a comparison.

## Shape

```
{
  "run_id":        <this run's identifier>,
  "previous_run":  <the identifier of the run this diffs against, or null>,
  "competitors": {
    "<name>": {
      "features":         [ { value, source, date, tier, observed_on } ],
      "pricing":          [ { value, source, date, tier, observed_on } ],
      "sentiment":        { rating, review_volume, direction, themes[], observed_on, source, tier },
      "hiring":           { scale, timing, alignment, recurrence, observed_on, source, tier },
      "exec_commentary":  [ { value, venue, date, source, tier, observed_on } ],
      "financials":       { revenue, growth, guidance, observed_on, source, tier },
      "geo":              { markets[], announced_vs_live, observed_on, source, tier }
    }
  },
  "our_state": { ... same axes for us, plus strategy/goal ... },
  "decisions": [
    { "id", "raised_in_run", "recommendation", "owner",
      "status": "open" | "in progress" | "done" | "dropped",
      "outcome_note" }
  ]
}
```

`competitors{}`, `our_state` and `decisions[]` keep exactly these names. A consumer that reads `decisions[]` to carry recommendations forward must find them under that key.

## Field discipline

- **Every field carries `observed_on` and a source.** A field with neither is not state; it is a guess with a timestamp.
- **A field that could not be re-observed keeps its prior value and is marked stale with its age.** Never silently refreshed. Never re-derived from memory. "Pricing unchanged" and "pricing not re-checked" are different findings and must not collapse into each other.
- **Tier travels with the value.** `h` hard (observed, sourced) · `s` soft (grounded estimate) · `i` inferred (analytical judgment) · `v` vendor-reported (the company's own claim about itself). Vendor-reported is a separate axis from confidence — it measures incentive, not certainty. Tiers are never blended, and an inference is never promoted to a fact between runs.
- **Where sources disagreed, the stored value is the range**, not a figure picked from it.
- **Our own figures carry a marked source too.** Run without internal data access, our numbers come from trade press; the state records that, so a later run does not read them as internals.

## Mode selection from state

- Prior state exists, the competitor set is materially unchanged, and the caller did not ask for the full study → **Scan**.
- No prior state, a materially changed competitor set, or an explicit request for the full study → **Review**.
- A materially changed set means a name added or removed, not a rename or a reordering.

## Decisions carry-forward

Every prior entry in `decisions[]` reappears in the new run's recommendations section with its status and what happened. A dropped item records why. This is what turns a report into a program — a decision that silently disappears between runs reads as one that was never taken.

## What never enters state

Report mechanics — cadence, audience, which mode ran, how the skill works. None of it is state, and none of it appears in the document.
