---
name: prioritize
description: Prioritize features, ideas, or initiatives against a stated goal by selecting the right framework and running the math. Use when the user says "prioritize this", "RICE these", "what should we build first to hit <goal>", "rank our backlog", "WSJF", "prioritize for <goal>", "which framework should I use", "which problem should we fix first", "rank by companies affected and severity", "prioritize by revenue", "rank by North Star", or "which project drives the most <metric>". It runs in two explicit modes: **plain** (rank by framework value, no goal) and **goal** (rank by how much each item moves a stated goal — for Sprntly, a North Star + secondary metrics). In goal mode, goal-alignment is a first-class scoring dimension, not an afterthought. Picks RICE / WSJF / Kano / MoSCoW for ranking solutions, **VoC Volume & Severity** for ranking problems by converging signals (companies affected, severity, analytics, churn, sales feedback from connectors), or **North Star Impact** to rank purely by each item's modeled effect on the single North Star metric (e.g. a revenue number per project), validates each input against evidence to guard against hallucinated or wrong assumptions, then computes and explains the ranking.
---

# Prioritize

## What it does
It ranks **solutions** (features/initiatives, via RICE/WSJF/Kano/MoSCoW), **problems** (via VoC Volume & Severity, when complaint and data signals converge), or items **purely by their modeled impact on the single North Star metric** (via North Star Impact — e.g. a dollar figure per project). Given a set of items to rank, it first establishes **the goal it's prioritizing toward** (because the same backlog ranks differently for "grow activation" vs "reduce churn" vs "win enterprise"), then *chooses the appropriate prioritization framework* for the situation (a step most teams skip), scores against the goal, and explains the resulting order — including ties, sensitivity, and where a different framework would change the answer.

**Two modes, chosen explicitly:**
- **Plain mode** — no goal. Rank purely by the chosen framework's value math. Use when there's no goal to steer by; the output says it's goal-agnostic.
- **Goal mode** — rank by *how much each item advances a stated goal*. The goal can be a single objective, or (the Sprntly default) a **North Star metric plus secondary metrics**, where each item is scored on its fit to each metric and the North Star is weighted highest. In Sprntly the goal/metrics come from `business-context`. Goal-alignment becomes a first-class scoring dimension that drives the ranking, not a sanity-check tacked on the end.

The script runs both deterministically, and goal mode prints the raw framework score *and* the goal-adjusted score side by side so the effect is transparent. Consolidates the many single-framework scoring skills into one that reasons about framework-fit and goal-fit.

## When to use / when NOT to use
- **Use** to rank a list of candidate features/initiatives/experiments.
- **Do NOT use** to triage a messy raw backlog into shape first (`backlog-triage`) or to make a single binary bet decision (`decision-memo`).

## Inputs
- **Required:** the items to prioritize.
- **Mode:** plain or goal. Default to **plain** unless a goal/metrics are provided or it's a Sprntly run (then **goal**).
- **For goal mode:** the **North Star metric** and any **secondary metrics** to prioritize toward (e.g. NS = activation; secondary = retention, expansion). In Sprntly, pull these from `business-context`. A single goal also works. Each item then needs a fit per metric (high/med/low).
- **Optional:** estimates (reach, impact, effort, confidence), constraints, framework preference, the North-Star weight. *If estimates are missing, ask for them OR run with labeled assumptions and show how sensitive the ranking is to them.*

## Method (methodology)
Goal anchoring + framework selection + the scoring math (RICE, WSJF, Kano, MoSCoW), with goal-alignment as a first-class dimension.
0. **Pick the mode.** **Plain** (no goal → rank by framework value, and say it's goal-agnostic) or **goal** (rank by goal-alignment). In goal mode, state the **North Star metric** and any **secondary metrics** and what "moving them" means; in Sprntly take them from `business-context`. Goal mode reframes the inputs: **"Impact" means impact *on these metrics*, not generic goodness** — an item that's high-impact for a different goal scores low here.
1. **Select the framework** by context:
   - **RICE** — many features, comparable units, want defensible quant. (Reach × Impact × Confidence ÷ Effort)
   - **WSJF** — delivery/SAFe context, cost-of-delay matters. (Cost of Delay ÷ Job Size)
   - **Kano** — feature *type* decisions: is this table-stakes, a linear performance lever, or a delighter? Survey-backed (see the Kano section below).
   - **MoSCoW** — release scoping (Must / Should / Could / Won't).
   - **VoC Volume & Severity** — ranks *problems* (not features) by converging Voice-of-Customer signals: number of **companies/accounts** affected, **severity**, and corroborating data — product **analytics**, **churn** data, **sales feedback** — pulled from connectors. Use when deciding which customer problem to fix first (see the VoC section below).
   - **North Star Impact** — ranks items by a **single number: each item's modeled effect on the one North Star metric** (e.g. if NS = revenue, the projected $ per project). Purest goal-driven ranking when there's one metric that matters and items can be translated into it (see the North Star Impact section below).
   Name why the chosen one fits; note the runner-up. **Before scoring, run the anti-hallucination checks below** so the framework isn't fed invented inputs.
2. **(Goal mode) Score goal-alignment as its own dimension.** Rate each item's fit to the **North Star** and to each **secondary metric** (High / Med / Low, or 0–1) with a one-line why. The North Star is weighted highest (default 0.7); secondary metrics split the rest. Items that don't move the metrics are surfaced to **cut or defer** even if their generic score is high. (Plain mode skips this step.)
3. **Gather/assume estimates**; label assumptions. Where the framework has an Impact term, score it as impact *on the goal*.
4. **Run the math** via `scripts/score.py` (deterministic, auditable). `--mode plain` ranks by framework only. `--mode goal` reads either a single `goal_fit` or a `metric_fit` map (NS + secondaries) per item, weights the North Star via `--ns-weight`, blends by `--goal-weight`, and prints raw + goal-adjusted scores so the effect is transparent. State the mode used.
5. **Rank + explain** — show scores AND goal-fit, call out near-ties, and the 1–2 items whose rank is most sensitive to a shaky estimate.
7. **Run on partial data; count convergence; flag contradictions (applies to EVERY framework).** Never refuse because an input is missing — run the framework with what's available, label each missing input, and lower Confidence accordingly. Report a **signal-convergence count** per item: how many of the expected inputs/signals were present, and of those, how many point the same direction (e.g. "VoC: 3 of 5 signals present; voice + analytics + sales converge"). **Highlight contradictions explicitly** — both *within* a framework (VoC: loud voice but flat analytics) and *across* frameworks (RICE ranks it low but North Star Impact ranks it high). A contradiction is surfaced for human judgment, never averaged into a smooth score.
6. **Goal sanity-check** — confirm the top of the list genuinely advances the goal; explicitly name any item that scores high on the framework but is **off-goal** (cut/defer it), and any item the goal elevates despite a mediocre framework score.


## How Kano works (since it's the one framework that classifies, not scores)
RICE/WSJF give a number to rank by; **Kano gives each feature a *category*** that says how customer satisfaction responds to it. It plots a feature on two axes — how well it's implemented vs. how satisfied users are — and sorts features into:
- **Must-be (basic):** absence enrages, presence earns nothing. Table stakes. → hit the threshold, then stop investing.
- **Performance (linear):** more is better, proportionally. The features users can name and will pay for. → invest in proportion to competitive pressure.
- **Attractive (delighter):** absence doesn't dissatisfy, presence delights. → a few differentiate you; you don't need many.
- **Indifferent:** users don't care. → don't build.
- **Reverse:** more of it makes users *less* happy. → removing it is the win.

**How a feature is classified — the two-question survey** (this is what makes Kano evidence-based, not a guess): for each feature ask both *"how would you feel if it had this?"* (functional) and *"how would you feel if it didn't?"* (dysfunctional), each on a five-point scale (like it / expect it / neutral / tolerate it / dislike it). Cross the two answers in the Kano table to land a category per respondent, then take the most-frequent category per feature (or Timko better-than/worse-than coefficients for a finer read). **Decision order:** cover every Must-be first → invest in Performance competitively → add a few Attractive delighters → drop Indifferent, remove Reverse. Note the time effect: **delighters decay into expectations** (a once-novel feature becomes table-stakes), so categories must be re-surveyed.


## How VoC Volume & Severity works (the framework for ranking problems)
RICE/WSJF rank *solutions*; VoC Volume & Severity ranks *problems* — and it's built for B2B reality, where **how many companies are affected** matters more than raw user counts. It scores each problem on signals that should agree if the problem is real, then looks hard at where they don't:
- **Voice** — complaint volume (as a rate) × **severity** (frustration, churn threats, blocker-vs-annoyance). From `third-party-feedback`.
- **Behavior** — product **analytics**: companies/accounts hitting it, drop-off / task-failure, and **churn** correlation. From analytics connectors.
- **Commercial** — **sales feedback** (lost deals, expansion blockers, account-team escalations from the CRM connector) and the revenue/logo value of who's affected.
- **Strategic fit** — does fixing it move the goal (0–1).

**The core move — convergence/divergence.** Compare the signals before blending:
- all converge (complaints + analytics + sales all point at it) → **validated priority, fix now**;
- loud complaints but analytics/sales are quiet → **vocal minority / perception gap** — investigate before building;
- few complaints but analytics/churn/sales scream → **silent killer** — companies leave without complaining; **elevate it**;
- everything quiet → noise.
A divergence is flagged and routed to `experiment-design`, never averaged away.

**Score (decomposed, runnable):** `Priority = Impact × Severity × Strategic-fit × Confidence`, with a **Trend** modifier. *Impact* = converged reach (companies affected + analytics reach + churn + sales signal; on divergence use the higher and flag). *Confidence* drops when only one signal exists or the data is sampled/open-web — so a scary score can't ride on weak data. Run it via `scripts/score.py --method voc` (factors 0–1, trend a modifier). The deep methodology lives in the `voc-volume-severity` skill, which this framework is the prioritization-side entry point for.


## How North Star Impact works (rank by one metric, as a number)
The most direct framework: translate every item into **its projected effect on the single North Star metric**, then rank by that number. No weighting across dimensions — if the NS is revenue, each project gets a revenue figure and the biggest defensible number wins.
1. **Name the NS metric and its unit** (e.g. $ annual revenue, # activated accounts, # retained logos). One metric only — if there are several, this isn't the right framework (use goal mode).
2. **Model each item's NS delta transparently.** Build a simple driver chain per item and show it — e.g. revenue from a fix = `affected_accounts × adoption_rate × ARPU`; revenue from churn-reduction = `accounts_saved × ARPU`. The model differs by item type; state the one used.
3. **State the drivers (real vs. assumed) and give a range** — low / expected / high — not a single false-precision figure. Inputs trace to connectors/data or are labeled `[ASSUMPTION]`.
4. **Rank by the expected NS number;** show the range so a close call between two items is visible.
5. **Sanity-check:** the model's logic is stated and debatable; flag any item whose rank hinges on one shaky driver.
Run it via `scripts/score.py --method northstar` (ranks by the modeled `ns_impact`, carries an optional low/high range).

## Guardrails against hallucination & wrong assumptions (every framework)
Prioritization math launders inputs into false confidence, so each input is checked before it's scored — **a number is never invented to fill a blank.** Per framework:
- **RICE** — *Reach* must trace to real data (analytics/segment counts) or be labeled `[ASSUMPTION]`; *Confidence* is the explicit hedge — low-evidence items get low confidence, not an optimistic guess; *Impact* in goal mode is impact *on the stated metric*, justified in one line, not generic goodness; *Effort* comes from engineering, not the PM's hope. If Reach and Effort are both guessed, the RICE score is flagged as low-confidence.
- **WSJF** — Cost-of-Delay components (user value, time-criticality, risk-reduction) and job-size are relative estimates; the skill states they're relative, refuses to present them as absolute, and flags any that lack a stated basis.
- **Kano** — categories are only valid from **real survey responses**. Without a survey, the skill labels the classification *hypothesized, not measured* and does not assert a category as fact; it never fabricates survey percentages.
- **MoSCoW** — "Must" is the abused bucket; the skill challenges every Must ("what breaks if this slips a release?") and rejects a Must that can't justify itself, so the scoping isn't built on an assumed urgency.
- **VoC Volume & Severity** — companies-affected, analytics, churn, and sales signals must each trace to a connector/source or be labeled assumed (never invented); **correlation ≠ causation** (a churn-correlated problem isn't proven to *cause* churn — the resolving experiment settles it); confidence is lowered when only one signal is present; survivorship is flagged (companies that already churned are invisible in the live signals).
- **North Star Impact** — the NS-metric figure (e.g. revenue) is **never fabricated**: every driver in the model is shown and traces to data or is labeled `[ASSUMPTION]`; output is a range, not false precision; modeled impact ≠ realized impact (validate post-ship); the metric must be a single concrete NS or the framework is the wrong choice.
- **All modes** — every estimate is labeled real vs. assumed; the ranking ships with a **sensitivity note** naming the 1–2 items whose rank flips on a shaky input; the goal/North Star must be a concrete metric (a vague goal produces a confidently wrong ranking) and is challenged if it isn't.

## Output spec
**The main artifact is always the ranked list** — the items arranged in priority order, ready to act on, led first. It is then **accompanied by a "how we scored it" table** so the ranking is reviewable, never a black box:
1. **The ranked list** (the deliverable) — items in priority order, each with its one-line reason.
2. **The scoring table** (for review) — one row per item showing the inputs and the decomposed score for the chosen framework: RICE → reach/impact/confidence/effort; WSJF → CoD components/job-size; Kano → category + survey basis; VoC → companies/severity/analytics/churn/sales + confidence; North Star Impact → the driver model + low/expected/high number. Mark every input real vs. `[ASSUMPTION]`.
3. **Signal-convergence column** — per item, `N of M signals present` and how many converge; missing inputs named (the framework still ran).
4. **Contradictions callout** — items where signals or frameworks disagree, stated plainly with the interpretation (e.g. "RICE low, North Star high → high-value/low-reach; RICE underweights it"). Never hidden, never averaged.
5. **Flags** — near-ties and the 1–2 items whose rank flips on a shaky input.
State the framework + mode used. Math via `scripts/score.py` (`--method rice|wsjf|voc|northstar`, `--mode plain|goal`, `--north-star`, `--ns-weight`, `--goal-weight`).

## Sprntly integration (optional)
- **Inputs from Sprntly:** the **goal from `business-context`** (so prioritization is scored against the company's actual stated goal); backlog items + confidence scores from the Monday Brief / outcome graph (Reach and Confidence can be data-derived, not guessed).
- **Outputs to Sprntly:** ranked backlog written back; the score components stored so re-ranking is reproducible as data updates.
- **Degrades to:** standalone; ask for estimates or assume-and-label.

## Quality checklist (the bar)
- [ ] The **mode is explicit** (plain vs goal). In goal mode the North Star (+ secondary metrics) are stated and "Impact" is scored against them; in plain mode the ranking is explicitly labeled goal-agnostic. The two modes don't bleed into each other (plain ignores goal data entirely).
- [ ] **Goal-alignment is a visible scoring dimension** (its own column), and how it was combined with the framework score is stated.
- [ ] Off-goal items that score high on the framework are **named for cut/defer**; on-goal items the framework undervalues are surfaced.
- [ ] The framework is *chosen* with a reason, not defaulted to RICE.
- [ ] **The ranked list is delivered as the main artifact, with a scoring table beside it for review** — never a bare list, never a table without the ordered list.
- [ ] **Runs on partial data** — never refuses for a missing input; reports `N of M signals present` and how many converge; missing inputs labeled and Confidence lowered.
- [ ] **Contradictions highlighted, not averaged** — disagreements within a framework and across frameworks are surfaced for judgment.
- [ ] Math is shown and reproducible (script), not asserted; estimate assumptions are labeled and sensitivity surfaced.
- [ ] **Anti-hallucination checks run for the chosen framework** — no input invented to fill a blank; Reach/Effort/survey/connector data traced or labeled assumed; Confidence reflects evidence; for Signal Triangulation, divergences are flagged not averaged and correlation≠causation is respected; a vague goal is challenged, not scored.

## Known gaps / limitations
- Garbage estimates → garbage ranking; the skill mitigates with sensitivity analysis but can't manufacture good inputs.
- Quant frameworks launder subjective inputs into false precision — the skill states this and shows the soft spots.
- Kano needs real survey data to be rigorous; without it the skill marks the classification hypothesized, not measured (see guardrails).
- Goal-alignment scoring is a judgment; the skill makes it explicit (one-line why per item) so it's debatable, but a wrong or vague goal produces a confidently wrong ranking — pin the goal to a concrete metric.

## Worked example

**Plain mode** — *"Rank: SSO, dark mode, bulk export, onboarding. No goal given."*
- Framework: RICE (comparable features, want defensible quant). Reach/Impact/Effort labeled real-vs-assumed; Confidence carries the hedge for the two items with guessed reach. Ranking by raw value, stated goal-agnostic — "this changes once you give me a goal." Sensitivity: bulk-export's rank depends on a guessed reach — flagged.

**Goal mode (Sprntly: North Star + secondary)** — *same items; NS = activation, secondary = retention.*
- Fit scored per metric: onboarding {activation: high, retention: med}; bulk export {activation: low, retention: high}; SSO {activation: low, retention: low}; dark mode {activation: low, retention: low}. NS weighted 0.7.
- Goal-adjusted ranking: **onboarding 246 (raw 280) > bulk export 160 (raw 336) > dark mode 81 > SSO 47.** Onboarding rises to #1 despite a lower raw score because it's the only strong fit to the activation North Star; bulk export survives on its retention fit; dark mode/SSO sink as off-goal. Flag: dark mode and SSO to **defer**.
- **Mode matters:** if the North Star were *retention* instead, bulk export would take #1 and onboarding drop to #2 — same backlog, same data, different order. That's the whole point of goal mode.
