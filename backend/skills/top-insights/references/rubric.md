# Rubric and linters

Two layers. The **linters** are mechanical pass/fail checks — run them on every brief and treat failures as blocking. The **rubric** is the judgment layer — score each card and the greeting, and revise anything that fails a hard gate (SKILL.md step 8).

## Deterministic linters (blocking — fail = do not emit)

Run these as code where possible; they're cheap and catch most defects.

### Grounding and totals
- **Grounding:** every number in a title, body, greeting, or what-to-build item maps to a field on a source finding (`pain.value`, `value.amount`/`range`, `reach.count`, or evidence). Any orphan number fails.
- **Totals integrity:** the greeting's "within reach" total equals the sum of the card value figures, within rounding. Backlog items do not count toward it. Mismatch fails.
- **No cross-channel averaging:** a figure equal to the mean of two channel figures, with neither present upstream, fails.
- **Provenance recorded:** every card's `audit.figure_provenance` names a source for each figure it renders. Missing fails.

### Freshness and cadence — the new failure surface
- **State legality:** a card's `state` is `new`, `updated`, or `carried_promoted`. A `carried` or `in_progress` finding rendered as a card fails.
- **Floor promotion limit:** at most one `carried_promoted` card per brief, and only when there would otherwise be zero cards. Two or more fails.
- **Slow-lane discipline:** a card whose source `refresh_interval_days > brief_interval_days` must have an `as_of` later than `ledger.last_brief_at`. A monthly source producing a card on a day its report didn't refresh fails.
- **Updated cards show the change:** a card with `state: updated` whose body's first beat doesn't state what changed fails.
- **Cadence framing:** the greeting's time language matches `cadence` — "this week" in a daily brief fails, as does "today" in a monthly one.
- **No padding:** card count never exceeds the number of findings in `new`/`updated` state, plus at most one floor promotion. Padding to hit a default count fails.

### Card shape
- **Title shape:** the title contains a finding element and a stake element (or an explicit qualitative stake when `value.amount` is null). Finding-only fails.
- **No prescriptive titles:** a title whose second half promises the reward of a fix — "fixing it protects", "the fix recovers", "claiming them adds" — fails. It must size the problem, not the solution.
- **Evidence beat present:** the body's third beat states what the finding rests on. A body ending in "review and approve the PRD" fails.
- **Release pace:** findings carded from a single `report_ref` in one cycle must not exceed `ceil(findings_count ÷ (refresh_interval ÷ brief_interval))`, floor 1, cap 2 — unless urgency is high, which bypasses. Over fails.
- **Report link present:** every card with a `report_ref` carries the report as its primary CTA. Missing fails.
- **Body length:** body ≤ 4 rendered lines at template width (≈ 480 characters). Over fails.
- **Self-containment:** the body's first sentence names a concrete subject, not a bare pronoun ("It"/"This"). A leading bare pronoun fails.
- **Type ∈ taxonomy** and **accent == the type's hex.** Mismatch fails.
- **Valence:** loss types (reliability, retention, competitive, compliance) never use a gain color; gain types (growth, momentum) never carry loss framing. Violation fails.
- **Tag is the category** label, optionally qualified with the type after a middot. A bare type tag where the category differs fails.
- **CTAs:** exactly two, primary then ghost. Primary is `View the full report` when `report_ref` exists, otherwise `View the evidence`. Ghost is `Generate PRD` when `prd_ref` is null, `View PRD` when it exists. A PRD in the primary slot fails.
- **No priority labels:** no "P0/P1" on the tag. Presence fails.
- **No meta-widgets:** no "N signals agree" string, no confidence bar as a card element. Presence fails.

### Selection profile
- **Gate before profile:** a finding excluded by the profile must not also be recorded as failing the objective gate, and vice versa. Conflating "unsound" with "not for this reader" fails.
- **Filters land on the backlog:** every finding removed by a hard filter appears in `backlog[]` with reason `excluded_by_profile` and its filter label. Silently discarded fails.
- **Multiplier clamp:** every preference multiplier is within 0.5–2.0. Outside fails — that is a filter in disguise and belongs in `hard_filters` where the reader can see it.
- **Preferences never exclude:** a finding may not be dropped solely for carrying a low multiplier. If it survives the gate and freshness and nothing outranks it, it appears.
- **Severity override announced:** a card with `audit.severity_override: true` must say on the card that it sits outside the reader's filters. A silent override fails.
- **Override rarity:** if the override fires on more than one card per brief, flag it — the severity bar is set wrong.
- **No silent learning:** a brief whose ranking used a multiplier not present in the stored profile fails. Behavioral signals may only produce entries in `pending_suggestions`; they may never affect this brief's ranking.
- **Deferral is not dismissal:** a deferred finding must carry `deferred_until` and must not count toward a dismissal streak or toward rotation exhaustion.
- **Audit answers "why":** every card records `profile_version`, `base_score`, and every labeled multiplier applied. Missing fails.

### Sources and categories
- **Source honesty:** chip count == distinct `sources` count; prose does not claim convergence when only one channel carried it.
- **Channel legality (customer_problems):** the headline pain stat does not originate from the `public` channel; a reach or volume claim does not originate from `direct`. Violation fails.
- **Tension surfaced:** a merged finding with `tension` set whose body doesn't narrate it fails.
- **Subscription respect:** no card from a category the recipient didn't subscribe to. Presence fails.
- **Silent degradation:** no prose mentioning an unavailable or unwired source. Presence fails.
- **Category cap:** ≤ 2 cards per category (unless it's the only subscription); ≤ 1 celebrate card, never in position 1 unless it's the only card.

### Report shelf and render
- **HTML always produced:** every run emits a rendered file at `brief.rendered_html`, including on quiet cycles. A run with no HTML fails.
- **HTML matches the object:** every card, figure, and CTA in the render appears in the `brief` object. Divergence fails — the object is the source of truth.
- **Shelf rows carry a run date:** every entry in `reports[]` has `ran_at` and renders it. A dateless row fails, because it implies a currency it may not have.
- **Shelf summaries are not composed here:** a row's `summary` must match the report's own top-line. A sentence generated by this skill fails, as does a sentence containing a figure that isn't in the report's summary. No summary upstream → render the row without one.
- **Shelf summaries stay high-level:** a shelf sentence carrying an actionable figure fails — if it were worth acting on it should have been a card.
- **Shelf is subscribed and available only:** no row for an unsubscribed category or a skill that hasn't run. An empty shelf renders nothing rather than a placeholder.
- **Shelf is not a slot:** shelf entries never count toward the card budget, the category cap, or the greeting total. Counting one fails.
- **Shelf position fixed:** below the cards, every time.

### Structure
- **Card count within cadence budget:** daily 0–5, weekly 0–7, monthly 0–7. Outside fails.
- **Backlog completeness:** every gated finding appears in `backlog[]` with a legal reason. A dropped finding with no backlog entry fails.
- **what_to_build grounding:** every such card backlinks to at least one finding id and introduces no new figure. A build recommendation duplicating a finding already carded above it fails — keep one. With `core_goal` null, no what_to_build cards are emitted.

## Rubric (scored — 0 / 1 / 2 per dimension; hard-gate dimensions must score 2)

| Dimension | Hard gate? | 2 (target) | 1 | 0 |
|---|---|---|---|---|
| Grounding | yes | every figure traced; ranges for projections | a figure with weak basis | an invented or false-precision figure |
| Freshness honesty | yes | nothing unchanged surfaced at all | a carried item reworded to feel fresh | old content presented as this cycle's news |
| Self-containment | yes | body reads fully with the title removed | one mild dependency on the title | body is meaningless without the title |
| Title: finding + stake | yes | both present, tight, stake sizes the problem | both present but clunky/long | stake missing, or phrased as a promised fix |
| Valence/color | yes | accent matches type and valence | minor off-tone | gain color on a loss |
| CTA correctness | yes | exactly two, correct labels & order for the category | minor label slip | wrong/missing/extra |
| Channel discipline | yes | each figure from the channel authoritative for it | a defensible but loose attribution | a public-forum stat presented as your customer base |
| Arc completeness | no | what's happening (or what changed) → what's at stake → what it rests on | one beat thin | no basis given, so the reader can't judge it |
| Tone | no | informs and lets the reader judge | slightly generic | prescribes a solution, or assigns homework ("you should build…") |
| Greeting | no | ≤3 lines, cadence-correct framing, totals right, names top plays | 4 lines or flat framing | defensive, wrong cadence framing, or totals wrong |
| Prioritization | no | strongest-leverage card first; fresh outranks carried | defensible but not ideal order | clearly mis-ordered |
| Restraint | no | weak signals suppressed; quiet cycles look quiet | one borderline card surfaced | noise, padding, or manufactured urgency |
| Profile fidelity | no | ranking reflects the reader's stated criteria and the audit shows how | criteria applied loosely | ranking ignores the profile, or applies criteria the reader never stated |
| Rotation | no | acted-on and exhausted items cleared; queue advanced | one stale repeat | the same cards every cycle |

**Revise rule:** if any hard-gate dimension scores below 2, rewrite that card (or the greeting) once and re-score. If it still fails grounding, drop the figure or the card rather than ship an ungrounded claim.

## The daily-cadence stress test

Before shipping any change to this skill, simulate seven consecutive daily briefs against a fixed corpus where only the reliability source refreshes. A correct implementation produces: cards on day 1, then progressively fewer, then quiet days with a one-line greeting and no cards — and the monthly competitive finding appears exactly once. If it produces five cards every day for a week, the freshness logic is broken regardless of how good each card reads in isolation.

## What to check against the goldens

After linting, compare voice and shape to `references/examples.md`:
- Do the titles read like the golden titles (pain stat, then value-of-acting)?
- Do the bodies follow the arc and stand alone?
- Does the greeting frame upside, and match the cadence?
- Does a cross-channel card use each channel for what it's authoritative for?
- Did you avoid every numbered anti-pattern in the counter-examples section?
