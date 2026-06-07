# Module 8 — Synthesis → decisions (the point of the whole thing)

**Question:** So what? Given everything above and *our strategy/goal*, what should we actually do?

**Why this is the whole skill.** A spreadsheet of competitor data drives nothing. Value comes from interpretation that forces a decision: connect a competitor's pricing change + a recurring review complaint + a traffic trend into one strategic move. The job is to answer the big "so what" for the team — to go from feature-comparer to strategist.

**Method**
1. **Pull the signal** from each module into three buckets, each judged against our goal (module 1):
   - **Learn from** — what a competitor does better that we should adopt/adapt.
   - **Close** — gaps where we're losing that we must fix to stay competitive.
   - **Exploit** — competitor weaknesses + white space where we can win (the highest-value bucket).
2. **Synthesize, don't list.** For each item write the *insight*, not the fact: not "Competitor X raised prices," but "the category's core pain is opaque pricing — we win by being transparent and usage-based." Tie each to the strategy goal.
3. **Rank by impact × feasibility.** Plot each candidate move on impact (against the goal) × feasibility (effort/time/risk). The top-right is what to do now; bottom-left is "note and move on." (Hand high-stakes single calls to `decision-by-traffic-lights`.)
4. **Convert to roadmap moves with owners.** Each top decision → a concrete product/positioning/GTM action, an owner, and the metric it should move.
5. **State what would change this** + the **refresh cadence** (CI decays; say when to re-run and which change-alerts to watch).

**Output (leads the report):** the ranked decisions — learn/close/exploit, each with insight, impact×feasibility, owner, and the metric — plus refresh cadence. Everything else in the report is the evidence base for this section.
**Data integrity:** a decision may rest only on evidence that is sourced or honestly labeled. Do not strengthen a recommendation by quietly upgrading an unknown or a 🅘 inference into a stated fact. If a key decision hinges on a number we don't have, say so plainly and make "pull that data" the first action.
**Confidence:** decisions are judgments built on tiered evidence — show which tier each rests on, and route the riskiest to `red-team-review` / `pre-mortem`.


---

## The PM build-decision layer (always produce this — it's what makes the report actionable)

Strategy buckets (learn/close/exploit) are necessary but not sufficient — a PM needs *features and a build queue*. After the buckets, always produce:

### A. TLDR (top of the report, ≤30-second read)
- **What changed / matters most:** the 2–3 highest-signal items (on a recurring run, lead with the *diff* since last time).
- **Top feature gaps:** the 2–4 gaps that actually cost us, one line each.
- **Recommendations:** the ranked Build / Skip / Reframe calls.
Write it so a PM who reads only the TLDR still knows what to build next and what to ignore.

### B. Head-to-head scorecard (how we do vs. each competitor)
For the jobs that matter to our buyer, score us vs. each rival: **win / parity / lose** (+ one-line why). If the user named a company to compare against, it gets its own column and the most prominent treatment. Render as a colored matrix.

### C. Feature-gap matrix — every gap classified Build / Skip / Reframe
For each capability a rival has that we lack (or vice-versa), classify:
- **Build** — the gap is *costing us* (losing deals / user complaints / stalled evaluations) AND it fits our strength. The roadmap items.
- **Skip** — a *theoretical* gap (no evidence it costs us) or wrong-buyer/off-strategy. Naming these stops us chasing competitors. **A gap with no evidence of cost is Skip by default.**
- **Reframe** — a *perception* gap where our approach is different but not inferior → fix with messaging/positioning, not engineering.
Each row: the gap · who has it · evidence it matters (deal-loss / complaint / eval-stall / theoretical) · classification · the "so what."

### D. What's heating up (trends → where the puck is going)
The momentum read, made directional: which capabilities/categories are *growing* (funding flowing in, fast ship cadence, rising traffic/search, repeated in reviews as wanted), and therefore where to get ahead. Separate **hard** trend signals (traffic/ratings/ship cadence) from **soft** (funding/positioning) and label which we have.

### E. Ranked build queue (the output a PM acts on)
The Build items from C, ranked by **impact × our strength** (not just impact) — so we build where we can win, not where we'd be a me-too. Each: rank · feature · why now (tie to D) · which gap it closes · effort/risk note. Skip and Reframe items listed separately so the "don't build" decisions are explicit.

### F. Recurring-run diff (when a prior run exists)
Lead the TLDR with what changed: new features shipped by rivals, pricing moves, traffic swings, new entrants, and whether any prior recommendation should flip. A bi-weekly cadence means most of the report is stable — surface the deltas.

**Evidence discipline carries through:** a gap is only "Build" if its cost-evidence is real (sourced deal-loss/complaint) or honestly labeled as an assumption to verify; never upgrade a theoretical gap to Build to make the queue look fuller. Momentum claims separate hard signals from soft, and missing data (e.g. no real traffic numbers) is named as the top open data-pull, not faked.
