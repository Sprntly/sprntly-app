# Query guide — answering follow-ups about a delivered review

After the review is delivered, users ask follow-ups. This governs how to answer them. Work out the intent, answer from the stored run, and be explicit when the stored run cannot support an answer.

## Where answers come from

Three artefacts, in this order:

1. **The run's `state`** (`references/state-spec.md`) — per-competitor features, pricing, sentiment, hiring, exec commentary, financials, geo, plus `our_state` and `decisions[]`. Every field carries `observed_on`, a source and a tier.
2. **The run's `metadata` rollup** — window, derived competitor set with the reason each name is in, launch counts by classification, threat counts by severity/timing/defence, benchmark counts, recommendation list.
3. **The captured `records`** — the individual dated observations behind the above. Use for anything needing an actual date, quote or source URL.

**Never answer from general knowledge about these companies.** If it was not observed in the run, say so and name what would need collecting.

## Intent map

| Question shape | Intent | Answer from | Must also say |
|---|---|---|---|
| "What did [competitor] ship?" · "what launched last month" | **launch filter** | records classified `net-new` / `parity` / `deprecation` / `beta` / `market` for that name | The window checked — and if they shipped nothing, that silence is the finding |
| "Which threats have no defence?" | **threat filter** | metadata threat rollup where defence = `None` | Severity and timing for each; a *removes us / none* threat should already carry a recommendation |
| "Did their pricing change?" | **pricing diff** | `competitors[name].pricing` history | Whether "no change" was observed or simply not re-checked — the two are different answers |
| "What's the status of last quarter's recommendations?" | **carry-forward** | `decisions[]` with status + `outcome_note` | Why a dropped item was dropped |
| "How do we compare on [dimension]?" | **benchmark cut** | the relevant benchmark row / radar dimension | That scoring is judgment while the facts underneath are sourced |
| "Who is selling against [complaint]?" | **sentiment cross** | sentiment section's who-sells-against-it column | A theme mapping to nobody is avoidable loss, not competitive pressure |
| "Where did that number come from?" | **provenance** | the field's `source` + `date` + tier | The tier in words, and for a vendor-reported figure, that it is the company's own claim about itself |
| "Why is [name] in the set?" | **scope** | metadata's derived set with the one-line reason each | Who was considered and excluded |
| "Is anything new since the report?" | **freshness** | `observed_on` across state | That the answer is as of the run, and that a fresh scan is one sentence away |

## Rules

**Answer the cut that was asked for**, not the whole review again. Then offer the next useful cut.

**A field that was not re-observed is not a field that did not change.** Lead with its age when answering from it.

**Never promote a tier in an answer.** An inferred placement stays inferred when quoted back. A soft estimate does not become a figure because a follow-up asked for one number.

**Ranges stay ranges.** If the run stored "$28–44B across reputable sources", the answer is that range and the reason for it, never a midpoint.

**Unknowns are stated as unknown.** Name what would need collecting — an open pull is a useful answer; a plausible number is not.

**A report-shaped ask is not a follow-up.** "Run the competitive review", "monthly competitor scan", "where do we stand vs competitors" mean a fresh run, not a read of the stored one.

**Nothing about the mechanics.** No cadence explanation, no "this came from the stored state", no description of how the skill works.
