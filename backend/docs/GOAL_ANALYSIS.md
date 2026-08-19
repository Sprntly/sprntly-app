# Goal Analysis (engine: Crucible) — implementation plan

**User-facing name:** Goal Analysis. **Internal/engine name:** Crucible — code lives
under `app/crucible/`, docs under `backend/docs/crucible/`. Users never see the word
Crucible (spec README, line 3).

**Source spec:** `backend/docs/crucible/` — read `README.md` and `CRUCIBLE-SPEC.md`
before writing code in this area. Section 1 of the spec (the ten invariants) is
not advisory; it is the product.

**Status:** Phase 0 complete (spike run against real data, 2026-08-17, results in
§2). Phase 1 not started.

---

## 1. What this is, and the one thing it must earn

Takes a business goal, reads every analysis the company already has, reconciles the
contradictions between them, and returns a small ranked set of recommendations with
sizes, confidence, and what was ruled out.

The claim it has to earn: **the finding that matters is usually the one requiring
two documents nobody read together.**

It is an eleven-stage deterministic pipeline with bounded LLM call sites and a human
approval gate — **not an agent loop**. Reproducibility is the whole differentiator
against a general LLM, and a loop that chooses its own next step cannot guarantee
any invariant below (spec F3).

### The decisions already made

| Decision | Choice |
|---|---|
| User-facing name | **Goal Analysis** |
| Company gating | `feature_flags.crucible`, **default OFF**, fails closed |
| User activation | Mode chip in the chat composer, **sticky per thread** |
| Output surface | Dedicated `crucible_runs` tables + its own panel |
| Phase 0 | Spike on real data before shippable code — **done, §2** |

---

## 2. Phase 0 — the spike, and what it found

Run 2026-08-17 against one staging tenant, read-only, no writes, no LLM calls.
Scripts: `backend/scripts/crucible_spike.py`, `crucible_verify.py`,
`crucible_probe.py`. These are throwaway research tools, not product code, and all
three take `--company` as an argument — no tenant is named or defaulted anywhere in
this repo.

**This section names no customer, no account and no tenant, and quotes nothing.**
That is deliberate and it is CONVENTIONS.md's rule (2026-08-08): never name a
customer in connection with a commercial or relationship fact, or with anything
extracted from their calls — drop the sentence, keep the engineering lesson. This
repo is public. Every engineering point below survives the anonymisation intact; if
you need the tenant to reproduce a number, it is in `~/sprntly-brain`, not here.

**The substrate:** 5,714 signals · 2,141 theme entities · 6,687 edges · Jan–Aug 2026.
Source mix `communication` 3,828 / `customer_voice` 1,821 / `project_mgmt` 30 /
`verbal_claim` 24 / `pm_manual` 10. **Zero** `analytics`, `revenue`, or
`outcome_measured` signals. 163 named accounts (94 customer-side, 69 prospect-side).
No numeric magnitude anywhere in `properties`.

That shape matters: the tenant is `enterprise_low_n` / `b2b_sales_led`. ARR sizing is
impossible from this corpus, so the spike used **named accounts as the goal currency**
and ran in what the spec calls corpus-only mode.

**The experiment:** build theme clusters exactly as `synthesis/convergence.py` does
(same themes, same signal→theme edges, same dedup, same recency decay), then score
each cluster twice — once with the shipped formula, once Crucible-faithfully. One
variable: the scoring rule.

### Result 1 — PASS, and the adversarial check earned its place

The spike surfaced a finding the shipped ranking places at **#631**. Described by its
shape, which is the part that generalises:

> **A third-party integration appears in the corpus as two mutually exclusive
> claims, months apart, in different document types.** One set of documents is
> capability description — the integration is stated as an existing, shipped
> feature. The other set is account activity — one account records it as a hard
> requirement that is "planned but not yet available", and roughly three months
> later it is still being given a two-week ETA to other accounts.

Two claim sets, each unremarkable alone and jointly a problem: the company is
describing something as done and promising it as imminent at the same time. **No
single account's thread contains it** — you only see it by reading a capability
statement against an account requirement, which is the two-documents shape the whole
engine is premised on, occurring on real data.

**The first framing of this finding was wrong, and verification killed it.** The spike
initially proposed "the two-week promise recurs over months." Pulling all 56
signals mentioning that integration in date order showed every ETA promise clustered
in a single week and mostly echoing one demo. Had that shipped it would have been
confident, well-sourced, and false — spec F1 exactly. **Adversarial verification is a
pipeline stage, not an optional extra.**

Honest precision: the detector produced **4 candidates, 1 real** — one was a
category rather than a finding, one was generic, one was mildly interesting. 25% is a
starting point, not a good number.

### Result 2 — a defect in the shipped brief scorer

`synthesis/scoring.py:88-98` documents its own contract as:

> `impact` is converged reach (**accounts affected** + analytics + churn + sales signal)

The only caller, `synthesis/convergence.py:220`, passes:

```python
impact=min(1.0, tc.breadth / 5.0),          # distinct SOURCE TYPES, not accounts
severity=min(1.0, tc.effective_weight / max(tc.signal_count, 1)),
```

Two consequences, both visible in the output:

1. **`severity` is a mean**, so a theme is *penalised* for carrying more evidence.
2. **`impact` is corroboration**, capped at 5 source types, and most themes sit at 2–3.

Nothing in the shipped score grows with amount of evidence or number of accounts
affected. The measured effect on this tenant: the top-ranked theme is backed by
**2 claims and names 0 accounts**, while a theme spanning 19 named accounts ranks
#199, one spanning 16 accounts with 101 supporting claims ranks #161, and the
cross-document finding above ranks #631.

This is I1's failure mode in production, and it is independent of whether we build
Crucible. **Fix it separately** — see §7.

### Result 3 — three things the spike learned that change the build

- **Corpus-only mode is the default here, not an edge case.** With no
  `outcome_measured` signals the solution leg of confidence is a constant, which
  makes the combined score useless for ordering. Band on the problem leg, cap at
  medium, and state why. Build this in Phase 1, not Phase 3.
- **Theme dedup is load-bearing.** Four differently-worded labels for one concept
  (same words, different order and punctuation) are one theme in four rows, splitting
  its accounts four ways so each looks smaller than it is. 2,141 themes → 2,067 on a
  crude label merge. The spec's "dedupe by mechanism, not by wording" is required,
  not polish.
- **Signal duplication inflates every count.** A single account's integration request
  appeared ~8 times across six days as near-identical rows.

### Verdict

Phase 1 is justified. The thesis holds on real data; the scoring difference is real
and large; and the two biggest risks (extraction/verification quality, and confidence
collapsing without outcome data) are now measured rather than assumed.

---

## 3. The ten invariants, and where each lands

These are correctness properties with tests, not preferences (spec §1).

| | Invariant | Phase | Enforcement |
|---|---|---|---|
| I1 | Impact never reads corroboration | 1 | **Flagship test:** `score_impact` byte-identical when `surfaced_by` is mutated |
| I2 | LLM proposes, deterministic code decides | 1 | No call site returns a score/rank/confidence — checkable in return types |
| I3 | Unmeasured is not zero | 1 | `None` propagates, renders "not measured"; test mixed-null aggregation, not just all-null |
| I4 | A source never votes outside its authority | 2 | Authority matrix over `connectors/catalog.py`; self-selected sources never magnitude-authoritative |
| I5 | Causal verbs require causal evidence | 1 | Deterministic lint, hard error, banned-verb corpus |
| I6 | Empty sources are closed silently | 1 | Output never references an unread source |
| I7 | Effort shows derivation or does not exist | 3 | `null` + reason under 3 comparables; tracker cycle-times supply the comparables |
| I8 | Assumed params visibly distinguished | 1 | Inline flag + assumptions section |
| I9 | Goal definition adopted or elicited, never inferred | 1 | Hard error entering Stage 1 unlocked; no LLM may set `locked` |
| I10 | Prioritisation never mutates impact/confidence | 3 | Fixture test runs pipeline with Stage 10 on and off, asserts byte-identical |

**Build the invariant assertions first, as executable tests, before any stage exists**
(spec build order, and the reason is that they are cross-cutting — built in parallel
they get violated at the seams, invisibly, because each stage's own tests pass).

---

## 4. Architecture

### 4.1 Gating — three layers

```
company visibility   feature_flags.crucible        default OFF, fails closed
       ↓
user activation      composer chip, per thread     default off
       ↓
run confirmation     Stage 0 goal lock (I9)        nothing expensive runs unconfirmed
```

`crucible_enabled(flags)` goes in `app/entitlements.py` next to its siblings, and
follows `ask_planner_shadow_enabled`'s shape, not `agents_enabled`'s: **explicit
`true` → ON; key absent → OFF; flags unknown → OFF.** Fail-open is right for "can
this tenant see a module they already had"; it is wrong for a new capability that
spends real money per run.

**The intent envelope is not touched.** With the chip on, the message posts to
`/v1/crucible/runs` and never reaches `chat_intent.resolve`. `INTENTS` gains no
member. The feature is removable by deleting one branch.

### 4.2 The chat affordance

`web/app/components/shared/ChatComposer.tsx` already has the right anatomy: a
`cx-head` that renders a removable chip, and a `+` menu.

- Third `+` item: **"Analyse a goal"** with an `experimental` tag. Rendered only when
  entitled — an unentitled company sees today's two-item menu exactly.
- Selecting it pins a mode chip in `cx-head` (same shape as `pinnedSkill`) and swaps
  the placeholder to *"Describe the goal — e.g. lift trial→paid conversion 15% this
  quarter"*.
- Sticky per thread, persisted via `conversations.crucible_mode`, so a reload and a
  follow-up both stay in the run.
- An entry on the chat landing empty state, because nobody discovers a `+` menu item.
- **No slash command** — ruled out by the no-slash-in-chat rule.

### 4.3 Run lifecycle

```
draft → resolving_goal → awaiting_confirmation → planning
      → awaiting_approval → running → ready | failed | cancelled
```

Copies the pattern proven by `routes/custom_artifacts.py:320`: durable row created
**before** the long call so the panel has an id to poll; a dedicated bounded executor
rather than `to_thread`'s shared default pool (a run holds its thread for minutes);
task held in a module-level set; orphan sweep on a recurring schedule with the age
gate derived from `MAX_ATTEMPTS × LONG_REQUEST_TIMEOUT_S`; `error_code` from a closed
set on failure, and failed rows **listed**, not filtered out.

Both human gates render through **QuestionPopup** (owner directive, 2026-08-16: every
choice the product asks for goes through it).

### 4.4 Data model

New tables, all `company_id`-scoped with RLS like every sibling:

| Table | Holds |
|---|---|
| `crucible_runs` | status, goal ref, conversation ref, timings, error_code, cost |
| `crucible_goal_definitions` | locked definition, source ref, hash, conflicts, provenance |
| `crucible_claims` | projected claims: type, strength, population, authoritative, `raw` |
| `crucible_findings` | statement, claim ids, impact, confidence, adjudication verdict |
| `crucible_ledger` | considered-and-not-prioritised, with resume state for `deepen` |
| `crucible_predictions` | band + stated range per recommendation — **written from run 1** |

**Not** an extension of `kg_signal`. The KG is load-bearing for briefs, PRDs, and
chat; adding claim semantics to it changes brief scoring for every tenant. Claims are
*projected* from signals (`kind` → claim type, `source_type` → strength + authority,
`properties` → population), which the spike proved is cheap and needs no LLM call.

`crucible_predictions` is cheap now and impossible to retrofit — you cannot recover
predictions you never logged (spec F8).

### 4.5 Cost control

The spec models no cost ceiling; the critique calls that out (weakness 10) and a
5,714-signal tenant is not the largest we will see. Per run: a claim cap, a
dispatched-analysis cap (default 4), and a token budget recorded on the run row.
Exceeding a cap is a `CoverageNote`, not a silent truncation.

---

## 5. Phase 1 — first staging ship

Ten PRs, each independently mergeable, each with tests. Sequence matters for the
first four; 6–9 can overlap.

| PR | Scope | Key tests |
|---|---|---|
| 1 | `app/crucible/types.py`, `invariants.py`, `lint.py`. Types + executable invariant assertions + causal lint. **Wired to nothing.** | 9 invariant tests; lint blocks each banned verb at each non-tested strength |
| 2 | Migration: the six tables + `conversations.crucible_mode` | RLS, company scoping, rollback |
| 3 | `crucible_enabled()` + `require_crucible_module` + staff panel toggle | absent → OFF, unknown → OFF, explicit true → ON |
| 4 | Claim projection + eval harness with a hand-labelled set | recall, strength accuracy, population accuracy **measured separately** |
| 5 | Stage 0 goal resolution: adopt from `kpi_tree` → metric registry → corpus → ask. Lock, hash, drift | conflicting definitions surface as conflict, never a selection; Stage 1 hard-errors unlocked |
| 6 | Stages 4–7: normalisation, substrate, two sweeps, adjudication. Theme dedup by mechanism | single authoritative claim retains full weight; conflict verdict on opposing authoritative claims |
| 7 | Stage 9 scoring + corpus-only mode | **flagship:** impact byte-identical under `surfaced_by` mutation; mixed-null aggregation |
| 8 | Adversarial verification pass (from Result 1 — not optional) | a finding refuted by date evidence is dropped |
| 9 | `/v1/crucible/runs` orchestration: durable row, bounded pool, sweep, cancel | orphan sweep on the scheduler path; double-submit is structurally impossible |
| 10 | Web: composer chip, `+` item, landing entry, per-thread persistence, run panel | unentitled company sees the unchanged menu; reload restores mode + run |

**Extraction is ~40% of total effort** (spec README F1) even though it occupies one
table row in the spec. The spike showed projection-from-signals avoids most of that
for tenants whose KG is already populated — but the eval harness in PR4 still gates
every prompt change, with no exceptions for "obviously safe" ones.

### Definition of done for Phase 1

A PM at an entitled company turns on Goal Analysis in chat, states a goal, confirms
the metric definition, and gets a ranked set of findings with confidence bands, an
expandable considered-list, and every unmeasured value rendered as "not measured".
All ten invariant tests green in CI. Staging Chrome-verified. **No prod cutover.**

---

## 6. Phases 2–4

- **Phase 2 — plan mode.** Stage 3 editable blocks under 600 words, `teach` writing to
  `company_context`, coverage map, ds-agent dispatch for analysis gaps, authority
  matrix over the connector catalog (I4). **Instrument edit rate, not approval rate** —
  an unread plan gives zero protection while looking like protection (F9).
- **Phase 3 — decisions.** Levers, unshipped-winner check, effort from tracker
  cycle-times (I7), Stage 10 prioritisation + ledger + `deepen` (I10), evidence
  artifacts (11b: every deep recommendation carries a chart, table, or verbatim quote;
  prose alone fails acceptance).
- **Phase 4 — compounding.** Calibration curve in-product, adoption tracking (shipped /
  modified / deferred / rejected), outcome write-back.

---

## 7. Separable from all of the above

The `convergence.py:220` defect (§2 Result 2) is a live bug in the shipped brief, not
a Crucible task. It should get its own PR and its own decision: the implementation
contradicts `voc_score`'s documented contract, and changing it changes every tenant's
brief ranking. **That is a product decision for Apurva, not a refactor** — the same
rule the spec applies to relaxing an invariant (F2).

---

## 8. What would make us stop

- Extraction/verification precision stays near the spike's 25% after PR4's eval set.
- PMs do not engage with the Stage 0 confirmation (Phase 1's cheap proxy for the
  plan-mode engagement risk that Phase 2 tests properly).
- Per-run cost lands somewhere a per-company allowlist cannot absorb.

---

## 9. Risks carried into the build

- **Determinism is weaker than the spec's marketing.** Scoring is deterministic given
  a substrate; extraction, tree generation, and clustering sit upstream of every
  score. Claim the accurate version (critique weakness 2).
- **Goal currency assumes linear value per unit.** Every adoption-driven number is
  overstated by an unknown amount (critique weakness 3). On the spike tenant there is
  no value-per-account data at all, so accounts-as-currency is itself an assumed
  parameter and must render under I8.
- **The authority matrix does not learn.** It encodes judgements true today, and
  nothing notices when a source's population scope changes (critique weakness 4).
