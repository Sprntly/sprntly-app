# Crucible: Build Specification

**Read this file completely before writing code.** It is self-contained. Every design decision that could be made differently has already been made and the reasoning is stated, so implement what is written rather than what seems reasonable.

**What you are building:** a service that takes a business goal and a set of connected evidence sources, produces an editable investigation plan for a product manager to steer, then reads every analysis that already exists in the company, reconciles the contradictions between them, and returns a small set of recommendations with sizes, confidence, and effort.

**The one-line claim it must earn:** the finding that matters is usually the one requiring two documents nobody read together.

---

## Table of contents

1. Non-negotiable invariants
2. Core concepts
3. Data model
4. The connector framework
5. Pipeline stages
6. Scoring
7. Output contract
8. Persistent stores
9. LLM call sites
10. Testing and acceptance
11. Build order

---

# 1. Non-negotiable invariants

These are correctness properties, not preferences. Each has a test in section 10. If an implementation choice conflicts with one of these, the invariant wins.

**I1. Impact never reads corroboration.** How many sources agree about a finding must not affect its size. Corroboration feeds confidence only. Violating this buries quiet high-value findings, which is the single failure mode this system exists to prevent.

**I2. The LLM proposes, deterministic code decides.** No language model call ever returns a score, a rank, or a confidence value. LLM calls return structured candidates. Scoring functions order them.

**I3. Unmeasured is not zero.** A cell with no data is `null`, never `0`. Coercing these is the most common way an analytics system misleads.

**I4. A source never votes outside its authority.** Each source is authoritative for specific claim types and contributes zero confidence outside them. Without this you get a weighted average of incommensurable evidence, which reads as rigour and is noise.

**I5. Causal verbs require causal evidence.** A deterministic lint blocks "causes", "drives", "leads to", "results in", "because of", "due to" on any claim not causally tested. Lint failure is a hard error, not a warning.

**I6. Empty sources are closed silently.** A store with no data is not read, not mentioned, and imposes no confidence penalty. The output never references a source that was not read.

**I7. Effort estimates show their derivation or do not exist.** If fewer than three comparable prior projects exist, return `null` with a reason. Never guess.

**I8. Assumed parameters are visibly distinguished from measured ones.** Any number in the output that came from judgement rather than data must be flagged inline and appear in the assumptions section.

**I9. The goal definition is adopted or elicited, never inferred.** If the company already defines the metric, that definition is adopted verbatim and shown to the user, who may change it in the clarification. If it does not, the user is asked. Either way the definition is confirmed before analysis runs, then locked and reused silently. No LLM output may set a definition to `locked`, and no code path may pass Stage 0 without one. Full specification in **CRUCIBLE-GOAL-RESOLUTION.md**. This invariant sits above the others: they protect the quality of the answer, this one protects the identity of the question.

**I10. Prioritisation never mutates impact or confidence.** Ordering reads frozen scores and never writes back to them. `scoreImpact` and `scoreConfidence` outputs are byte-identical whether Stage 10 runs or not. Where effort cannot be derived from comparables (I7), the item is returned `unrankable` with its reason and is never assigned a fabricated effort to complete a score.

---

# 2. Core concepts

## 2.1 Goal currency

Every finding is sized in the unit of the goal metric, never its native unit.

```
impact = |affected_population ∩ goal_population| × movable_gap × value_per_unit
```

Three properties, all enforced in code:

- **Value-weighted, not volume-weighted.** A segment that is 2% of users and 60% of revenue scores on the revenue.
- **Gap-based, not level-based.** What is movable, not what is large.
- **Independent of source count.** See I1.

| Goal metric | Currency | Sizing question |
|---|---|---|
| Revenue | ARR dollars | How much revenue does this touch? |
| Retention | retained users, or retention points | How many at-risk users? |
| Engagement | sessions or active days | How much usage does this create? |
| Activation | activated accounts | How many cross the value threshold? |
| Acquisition | new users or CAC dollars | How many won or lost? |
| Complaints | contacts | How many support contacts? |
| Cost | cost dollars | How much operating cost? |
| Velocity | cycle-time days | How much throughput consumed? |

The population intersection does real work. A finding affecting EU enterprise scores zero against a US self-serve goal regardless of size.

## 2.2 Claims

Every piece of evidence, from any source, normalises to a claim. This is the atom the whole system operates on.

## 2.3 Evidence classes

Six roles evidence can play. Connectors declare which they feed; stages declare which they require.

`magnitude` · `causal` · `diagnostic` · `comparative` · `feasibility` · `preference`

## 2.4 Confidence

**One number, shown as a band.** High, medium, low. Decimals exist internally for sorting and never appear in output.

It decomposes internally into problem certainty (is this real and correctly sized) and solution certainty (will this fix move it), and **the weaker of the two is named in prose**. That sentence is the only reason the decomposition exists.

## 2.5 Levers

A lever is a candidate intervention with three possible states: `known_works`, `unknown`, `known_fails`. Known failures record **why**, and only `mechanism_invalid` is permanent. Every other reason carries a reactivation condition checked on each run.

---

# 3. Data model

TypeScript. This is the contract; do not add fields to these without updating the spec.

```typescript
// ============ GOAL ============

export type GoalCurrency =
  | 'arr_dollars' | 'retained_users' | 'retention_points' | 'sessions'
  | 'activated_accounts' | 'new_users' | 'contacts' | 'cost_dollars' | 'cycle_days';

export type PoliticalWeight = 'board_commitment' | 'planning_input' | 'exploration';

export interface Goal {
  id: string;
  companyId: string;
  metric: string;
  metricSourceRef: string | null;
  currency: GoalCurrency;
  direction: 'up' | 'down';
  targetDelta: { value: number; unit: 'absolute' | 'relative' };
  horizonWeeks: number;
  population: PopulationFilter;
  constraints: string[];
  committedWork: CommittedItem[];      // REQUIRED. [] is valid, undefined is an error.
  politicalWeight: PoliticalWeight;
  successOwner: string | null;
  ownedSurface: string | null;         // which team's scope, if narrower than the metric
}

export interface PopulationFilter {
  segments: Record<string, string[]>;
  estimatedSize: number | null;
}

export interface CommittedItem {
  id: string; title: string; team: string;
  engineerCount: number; shipsAt: string;
  targetBranch: string | null;
}

// ============ CLAIM ============

export type ClaimType =
  | 'magnitude' | 'mechanism' | 'preference' | 'constraint' | 'direction'
  // Execution evidence. See section 4.5.
  | 'existence'      // this was built / shipped / exists in the product today
  | 'attempt';       // this was tried / committed / abandoned / reverted

export type EvidenceStrength =
  | 'causally_tested' | 'measured' | 'correlated' | 'inferred' | 'reported';

export const STRENGTH_SCORE: Record<EvidenceStrength, number> = {
  causally_tested: 1.00, measured: 0.90, correlated: 0.60, inferred: 0.40, reported: 0.25,
};

// Half-life in days for confidence decay, BY CLAIM TYPE.
// Competitive facts rot fast. Structural facts do not.
export const DECAY_HALFLIFE_DAYS: Record<ClaimType, number> = {
  magnitude: 180, mechanism: 540, preference: 270, constraint: 120, direction: 90,
  // Execution facts are checkable at read time, so they do not decay. They are
  // either still true in the repo and the tracker or they are not. Re-read
  // instead of discounting.
  existence: Infinity, attempt: Infinity,
};

export interface Claim {
  id: string;
  assertion: string;
  type: ClaimType;
  subject: string;
  subjectClusterId: string | null;
  population: PopulationFilter;
  populationValue: number | null;      // in goal currency
  magnitude: number | null;
  direction: 'positive' | 'negative' | 'neutral';
  strength: EvidenceStrength;
  sourceId: string;                    // connector id, NOT an enum. See section 4.
  artifactId: string;                  // which document or query this came from
  artifactType: string;                // 'ds_report' | 'qbr_deck' | 'ticket' | ...
  authoritative: boolean;              // computed from the registry
  observedAt: string;                  // ISO. REQUIRED. Drives decay.
  raw: unknown;                        // original payload, always retained
}

// ============ SUBSTRATE ============

export interface Cell {
  id: string;
  leafId: string;
  segment: Record<string, string>;
  valueInCurrency: number | null;      // null = UNKNOWN. See I3.
  sampleSize: number;
  sampleAdequate: boolean;
  excludedFromSweep: boolean;          // set when !sampleAdequate
  trend: number;
  variance: number;
  bestComparableId: string | null;
}

// ============ FINDING ============

export interface Finding {
  id: string;
  statement: string;                   // must pass the causal lint
  cellRefs: string[];
  claimIds: string[];
  surfacedBy: SweepId[];               // CONFIDENCE INPUT ONLY. See I1.
  impact: Impact;
  confidence: Confidence;
  matchedLevers: MatchedLever[];
}

export interface Impact {
  value: number;
  currency: GoalCurrency;
  affectedPopulation: number;
  movableGap: number;
  valuePerUnit: number;
  nativeUnits: Record<string, number>;
  assumedParams: AssumedParam[];       // See I8.
}

export interface AssumedParam {
  name: string;                        // 'recovery_rate'
  value: number;
  basis: string;                       // 'sampled 400 rejected inputs'
  plausibleRange: [number, number];
  impactAtLow: number;                 // what the headline becomes at the low end
}

export interface Confidence {
  band: 'high' | 'medium' | 'low';
  score: number;                        // internal only, never rendered
  weakestLeg: 'problem' | 'solution';
  weakestLegReason: string;             // rendered in prose
  components: Record<string, number>;
  blockers: string[];
}

// ============ LEVER ============

export type LeverState = 'known_works' | 'unknown' | 'known_fails';

export type FailureReason =
  | 'wrong_segment' | 'wrong_time' | 'poor_execution'
  | 'precondition_absent' | 'mechanism_invalid';   // last is permanent

export type LeverType =
  | 'build' | 'guide' | 'notify' | 'simplify' | 'automate'
  | 'package' | 'motion' | 'operate' | 'acquire' | 'retain' | 'rollout' | 'rollback';

export interface Lever {
  id: string;
  companyId: string | null;            // null = corpus-level prior
  name: string;
  mechanism: string;                   // dedupe key. NOT the name.
  type: LeverType;
  preconditions: Precondition[];
  observedEffect: { value: number; currency: GoalCurrency; segment: string } | null;
  observedAt: string | null;
  rolloutCoverage: number | null;      // 0..1. Drives the unshipped-winner check.
  failureReason: FailureReason | null;
  reactivationCondition: string | null;
}

export interface Precondition {
  key: string;
  operator: '>' | '<' | '==' | 'in';
  value: number | string | string[];
}

export interface MatchedLever {
  leverId: string;
  preconditionMatch: 'full' | 'partial' | 'none';
  triage: 'ship' | 'pilot' | 'probe_problem' | 'park';
  effortWeeks: { estimate: number | null; derivation: string };
  prerequisiteIds: string[];
}
```

---

# 4. The connector framework

**This is the most important section for long-term maintainability.** Connectors must be addable without touching pipeline code. Nothing in stages 4 through 10 may reference a specific source by name.

## 4.1 The manifest

Every connector ships a declarative manifest. This is the entire integration contract.

```typescript
export interface ConnectorManifest {
  id: string;                          // 'zendesk', 'amplitude', 'ds_agent'
  displayName: string;
  version: string;

  // ---- WHAT IT KNOWS ----
  evidenceClasses: EvidenceClass[];
  authoritativeFor: ClaimType[];       // claim types this source may vote on
  neverAuthoritativeFor: ClaimType[];  // explicit, for documentation and lint

  // ---- WHO IT REPRESENTS ----
  // Critical for the population intersection. A source that only sees paying
  // customers must not be treated as speaking for all users.
  populationScope: {
    describes: string;                 // 'users who contacted support'
    isRepresentativeOf: string | null; // 'all users' | null if self-selected
    selectionBias: 'none' | 'self_selected' | 'sampled' | 'census';
  };

  // ---- FRESHNESS ----
  latency: 'realtime' | 'daily' | 'weekly' | 'monthly' | 'quarterly' | 'static';
  defaultStrength: EvidenceStrength;   // ceiling on claims from this source

  // ---- FETCHING ----
  auth: { type: 'oauth2' | 'apikey' | 'basic' | 'none'; scopes?: string[] };
  capabilities: ConnectorCapability[];

  // ---- EXTRACTION ----
  extraction:
    | { mode: 'schema'; mapping: SchemaMapping }      // structured, deterministic
    | { mode: 'llm'; promptRef: string };             // unstructured, LLM-parsed

  // ---- COST ----
  costPerFetch: 'free' | 'cheap' | 'expensive';
  rateLimit: { requestsPerMinute: number };
}

export interface ConnectorCapability {
  name: string;                        // 'query_metric', 'list_documents', 'search'
  answers: ClaimType[];
  requiresParams: string[];
  dispatchable: boolean;               // can the engine ASK this source to run something new?
}
```

## 4.2 Capability negotiation

The pipeline never asks "what does Zendesk have." It asks the registry a question and the registry answers with whoever can serve it.

```typescript
interface ConnectorRegistry {
  // "Who can tell me about magnitude for this population?"
  resolve(query: {
    claimType: ClaimType;
    evidenceClass?: EvidenceClass;
    population?: PopulationFilter;
    authoritativeOnly: boolean;
  }): ConnectorManifest[];

  // "Who can I ask to run a new analysis?"
  dispatchable(claimType: ClaimType): ConnectorManifest[];

  // "What can nobody answer?"
  coverageGaps(required: ClaimType[]): CoverageGap[];
}
```

This is what makes the system extensible. Adding a connector adds capability; no stage changes.

## 4.3 Automatic onboarding of a new connector

When a company connects a source the system has not seen, run this. It is one LLM call plus deterministic checks, confirmed once by a human, then cached forever.

```
1. INTROSPECT
   Pull the source's schema, a sample of 200 records, and any API description.

2. CLASSIFY (LLM call, see 9.7)
   Propose: evidenceClasses, authoritativeFor, populationScope, defaultStrength,
   latency. The prompt includes the full authority matrix of known connectors as
   few-shot examples, so classification is anchored to existing conventions.

3. VALIDATE (deterministic)
   - authoritativeFor and neverAuthoritativeFor must not intersect
   - a source with selectionBias 'self_selected' MUST NOT be authoritative
     for 'magnitude'. This rule alone prevents most bad integrations.
   - a source with latency 'quarterly' or 'static' cannot be authoritative
     for 'direction'
   - defaultStrength cannot exceed 'measured' unless the source is an
     experiment platform

4. CONFIRM
   Present the proposed manifest to the admin in plain language:

     "Connected Gong. I'm treating it as authoritative for what customers
      want and why deals stall, and not for how many customers are affected,
      because it only covers accounts that took a sales call. Sound right?"

   One screen, editable, confirmed once.

5. PERSIST
   Write to connector_manifests. Never ask again.
```

**The self-selection rule in step 3 is load-bearing.** Reviews, tickets, social listening, and sales calls all describe self-selected populations. Letting any of them vote on magnitude is how a system ends up telling a company that its loudest problem is its biggest one.

## 4.4 Business-model defaults

Trust weights are inferred, never elicited. Detected from company shape at first run and stated in the plan for correction.

```typescript
export const MODEL_TRUST_DEFAULTS: Record<BusinessModel, TrustAdjustments> = {
  b2b_sales_led:   { up: ['crm', 'sales_notes', 'call_recordings'], down: ['surveys', 'aggregate_analytics'] },
  b2b_product_led: { up: ['product_telemetry', 'funnel_analytics'], down: ['sales_notes'] },
  consumer_scale:  { up: ['behavioral_analytics', 'experiments'],   down: ['reviews_for_magnitude', 'social'] },
  marketplace:     { up: ['both_sides_separately'],                 down: ['silence_as_signal'] },
  enterprise_low_n:{ up: ['all_qualitative'],                        down: ['statistical_significance'] },
  developer_tool:  { up: ['telemetry', 'community_signal'],          down: ['support_tickets'] },
  regulated:       { up: ['constraint_sources'],                     down: ['unconstrained_generation'] },
};
```

Additional shape-based inferences, applied on top:

| Observed | Inference |
|---|---|
| Support contact rate < 0.1 per account per year | Tickets are near-uninformative. Discount heavily and say so. |
| Experiment count > 50 in 24 months | Causal evidence is available. Weight up, and always run the unshipped-winner check. |
| Top 10% of accounts > 40% of revenue | Concentrated. Switch analysis to named accounts. |
| Value distribution near-uniform | No concentrated segment exists. Weight the long-tail aggregation heavily. |
| Two-sided transaction flow | Marketplace. Split every branch, and treat silence on either side as uninformative. |

**Floor every weight at 0.2.** A source weighted to zero is a source that can never surprise you.

## 4.5 Execution sources: the codebase and the tracker

Most sources describe **users**. Two describe **the company itself**, and they are treated as their own evidence class because they answer a different kind of question and carry a different kind of authority.

| Source | Examples | What it uniquely knows |
|---|---|---|
| Code | GitHub, GitLab, Bitbucket. PRs, merges, reverts, feature flags, release tags, file history | What actually exists in the product today, when it landed, what got quietly taken back out |
| Tracker | Jira, Linear, Asana, Shortcut. Tickets, epics, status, assignee, transitions, resolution reason | What was committed to, what is in flight, what was tried before, what was abandoned and why |

### Why these matter disproportionately

**They close the loop between claim and reality.** Every other source tells you what someone believes. These two tell you what was actually done. A research readout saying users want bulk export is a claim. A merged PR shipping bulk export eight months ago is a fact, and it turns the readout from a recommendation into a discovery problem.

**They are the only source for `committedWork`.** Stage 1 hard-errors when `committedWork` is undefined, because a plan that ignores in-flight work is fiction. The tracker is where that lives. Without a tracker connector, the engine is guessing at the one thing it refuses to guess at.

**The tracker is a rejection ledger the company already built.** Abandoned tickets, closed-won't-do, and epics that died in refinement are a record of what this org has already decided against, with dates and often with reasons. Seeding the `rejection_ledger` from it means the engine does not recommend, in week one, the thing the team killed last quarter. That single behaviour is most of the difference between reading as informed and reading as naive.

**Code makes the unshipped-winner check real.** Stage 8 looks for experiments that won and never fully rolled out. Feature flag state and revert history live in the repo. Silent reverts, meaning something shipped, worked, and was pulled without a decision record, are the highest-yield finding in the whole design and they are invisible without code access.

**They give effort estimates a derivation.** I7 requires effort to show its basis in at least three comparable prior projects, or return null. The tracker holds cycle times on real past work. This is the only realistic path to satisfying I7 rather than returning null forever.

### Authority

Strictly bounded. These sources are authoritative about the company and never about users.

```typescript
// Code connector
authoritativeFor:      ['existence', 'attempt', 'constraint'],
neverAuthoritativeFor: ['preference', 'magnitude', 'mechanism'],

// Tracker connector
authoritativeFor:      ['attempt', 'existence', 'constraint'],
neverAuthoritativeFor: ['preference', 'magnitude'],
```

**The failure mode this prevents.** A Jira ticket says "users are churning because of slow export." That is one engineer's framing entered into a text field, and it is not evidence about users at all. Ticket titles and PR descriptions are full of causal-sounding assertions about user behaviour that no one measured. Extracted naively, they enter the substrate looking like findings.

So the rule: from these sources, extract **what was done**, not **why someone said it was done**. Stated rationale is retained on the claim as context and is barred from voting on `mechanism` or `preference`.

### Population scope

```typescript
populationScope: {
  describes: 'work this organisation performed',
  isRepresentativeOf: null,          // never speaks for users
  selectionBias: 'census',            // complete for what it covers
}
```

Census within its own domain, meaning if it is not in the repo it was not built. That completeness is what makes negative findings from these sources trustworthy: **"this was never actually shipped" is a strong claim here and a weak claim anywhere else.**

### Extraction

Mostly `mode: 'schema'`, which is unusually good news. Ticket status, transition history, merge state, flag state, and file paths are structured fields and can be read deterministically, avoiding the extraction risk that dominates the rest of the system. Only free-text bodies go to `mode: 'llm'`, and those are extracted for `attempt` and `existence` only.

### Derived signals worth computing explicitly

| Signal | How | Why it matters |
|---|---|---|
| Silent revert | Merged, then reverted, no linked decision record | Highest-priority finding class in the design |
| Abandoned commitment | Ticket opened, worked, then closed unresolved or stale past a threshold | Seeds the rejection ledger with reactivation conditions |
| Shipped but unadopted | Code exists, flag at 100 percent, no corresponding usage metric movement | Distinguishes "we never built it" from "we built it and nobody found it", which produce opposite recommendations |
| Repeat attempt | Same area touched three or more times across quarters | Structural problem, not a feature gap |
| Cycle time by area | Tracker timestamps grouped by component | The comparables that let I7 return a real number |

### Privacy and enterprise note

Code access is a heavier ask than document access and will get its own security review. **Crucible needs metadata far more than source.** PR titles, file paths, merge and revert events, flag state, and timestamps deliver nearly all the value above. Diff contents deliver very little. Ship a metadata-only mode for the code connector and lead with it, because it turns "read our source code" into "read our commit log," which is a materially easier conversation.



## 4.6 Graceful degradation

A run must survive a source failing. One connector timing out degrades the run, it does not end it. Three error classes, three responses, and conflating them is what produces brittle hard-fail behaviour.

| Class | Examples | Response |
|---|---|---|
| **Transient** | Timeout, rate limit, 5xx | Retry per the manifest's declared policy. Exhausted retries become structural. |
| **Structural** | Connector down, auth expired, metric removed, permission denied | Walk the declared fallback ladder. Continue the run. Record a coverage note. |
| **Semantic** | Query returned zero rows, empty population | **Never an error.** This is a coverage gap or a finding. Under I3 it is `null`, never `0`, and it routes into the pipeline, not the error handler. |

The third row matters most: "the query returned nothing" is information about the company, not a system fault.

**Fallbacks are declared, not chosen.** Each connector manifest carries an ordered fallback ladder walked by deterministic code. No LLM call site receives an error payload or selects a recovery path, which preserves I2 and reproducibility.

```typescript
interface ConnectorManifest {
  // ... section 4.1
  resilience: {
    retry: { attempts: number; backoffMs: number; retryOn: ErrorClass[] };
    fallbacks: FallbackStep[];         // ordered, walked deterministically
    onExhausted: 'degrade' | 'block';  // 'block' only for goal-critical sources
  };
}

interface FallbackStep {
  condition: string;                   // 'auth_failed' | 'metric_missing' | 'timeout'
  action: 'use_cached' | 'use_alternate_capability' | 'use_corpus_estimate' | 'skip';
  maxAgeDays?: number;
  alternateCapability?: string;
  confidenceCeiling: EvidenceStrength; // caps every claim obtained this way
}
```

`onExhausted: 'block'` is reserved for the goal's own metric source and the tracker connector supplying `committedWork`. Everything else degrades.

**Degradation is always visible.** Every degradation writes a `CoverageNote` that renders where it affects a finding, and in the coverage section otherwise. Silent degradation is worse than failure, because a quietly thinner run looks identical to a complete one.

```typescript
interface CoverageNote {
  sourceId: string;
  intended: string;                    // 'ticket volume by segment, last 90 days'
  actual: string;                      // 'cached snapshot, 14 days stale'
  reason: string;
  confidenceImpact: EvidenceStrength;  // ceiling applied
  affectedFindingIds: string[];
}
```

A claim obtained through a fallback inherits that step's `confidenceCeiling` automatically. Cached data past `maxAgeDays` caps at `reported`; a corpus estimate substituting for telemetry never reaches `measured`.

---

# 5. Pipeline stages

Twelve stages. Stage 0 resolves the goal definition. Stages 1 to 3 are Phase A (understand and plan). Stages 4 to 11 are Phase B (execute). Stage 10 is the decision point: it turns a scored set into an ordered one.

## Stage 0. Goal resolution

```typescript
resolveGoal(rawGoalText: string, ctx: CompanyContext): Promise<GoalDefinition>
```

Five steps: lookup in `goal_definitions`, search connected metric registries, search the corpus for a definitional statement, ask if none of those resolve, then ground against live numbers and confirm. Adopt an existing definition verbatim where one exists. Never pick between conflicting definitions, surface the conflict. Once locked, reuse silently on every later run and re-open only on hash drift.

**Hard error** if Stage 1 is entered with `status !== 'locked'`.

Full specification, including data model, ask templates, drift handling, and goal-shape coverage, in **CRUCIBLE-GOAL-RESOLUTION.md**.

## Stage 1. Intake

```typescript
parseGoal(input: string, ctx: CompanyContext): Promise<{ goal: Goal; questions: ClarifyingQuestion[] }>
```

LLM parses free text into a partial `Goal`. Then the question taxonomy runs deterministically:

| Where the answer lives | Action |
|---|---|
| In their data | **Never ask.** Go look. Asking something retrievable destroys credibility faster than a wrong answer. |
| Inferable with acceptable risk | Propose with reasoning visible, allow correction. |
| Only in a human's head | Ask. Maximum four. |

```typescript
const RETRIEVABLE = ['metricSourceRef', 'committedWork', 'population'];
const INFERABLE   = ['currency', 'horizonWeeks', 'targetDelta', 'ownedSurface'];
const ASK_ONLY    = ['politicalWeight', 'constraints', 'successOwner'];
```

Every question carries the fork it resolves:

```typescript
interface ClarifyingQuestion {
  field: string;
  question: string;
  fork: { ifA: string; thenA: string; ifB: string; thenB: string };
}
```

**Hard error** if `committedWork === undefined` after connector resolution. A plan that ignores in-flight work is fiction. `committedWork` comes from the tracker connector (4.5); with no tracker connected, this must be supplied by the user and flagged as an assumed input, never defaulted to empty.

## Stage 2. Grounding

```typescript
groundMetric(goal, registry): Promise<GroundedMetric>
computeConcentration(metric, dimensions): ConcentrationReport[]
readTrajectory(metric): TrajectoryReport
```

```typescript
interface GroundedMetric {
  sourceRef: string | null;
  baseline: number;
  variance: number;
  seasonalityIndex: number[];          // 52 weekly multipliers
  minimumDetectableEffect: number;
  detectable: boolean;
  valuePerUnit: number;
}

interface TrajectoryReport {
  direction: 'rising' | 'flat' | 'declining';
  changePoints: { date: string; magnitude: number; shape: 'step' | 'gradual' }[];
  decomposition: { component: string; contribution: number }[];
  reframe: string | null;              // e.g. "this is expansion, not retention"
}
```

**MDE, deterministic:**

```
MDE = (1.96 + 0.84) × sqrt(2 × variance / n_per_period × horizon_periods) / baseline
```

If `detectable === false`, the plan must lead with it. Corvid-shaped companies (low N, high variance) need to know the deliverable changes before they read anything else.

`readTrajectory` is the highest-value cheap operation in the system. In the worked examples it reframed the goal twice before any analysis ran: flat revenue turned out to be per-customer decline masked by new logo growth, and an NRR problem turned out to be an expansion problem with healthy churn.

## Stage 3. Plan mode

The plan is an object of addressable blocks. Each is independently editable and re-renders alone.

```typescript
type BlockId =
  | 'GOAL' | 'TRAJECTORY' | 'DETECTABILITY' | 'CONCENTRATION'
  | 'DISPATCH' | 'INVESTIGATION' | 'TRUST' | 'COMMITTED' | 'ASK' | 'ASSUMPTIONS';

type PlanEdit =
  | { move: 'steer';  blockId: BlockId; instruction: string }
  | { move: 'ask';    blockId: BlockId; question: string }
  | { move: 'teach';  term: string; definition: string }
  | { move: 'answer'; field: string; value: unknown };
```

`teach` writes to `company_context` and persists across all future runs. This is the durable-context asset and its store is a first-class schema, not a blob.

**Length target: under 600 words.** The plan is read or it is worthless. Each investigation line is one action plus its decision fork, in two sentences:

> **1. Settle mix versus product.** If the decline survives holding source constant, it's product and the rest of this matters. If not, your funnel is fine and the goal belongs to growth.

**Repeat runs** load the previous approved plan and render a diff only.

**Instrument edit rate, not approval rate.** If under a third of plans are edited, the format is wrong and everything downstream is premature.

**The plan also states the decision rule.** Before any analysis runs, the plan names the prioritisation framework and its source, the depth cap, and the commitment that everything below the cap is still listed and expandable. A framework disclosed only after results exist cannot be told apart from one chosen to fit them. See Stage 10d.

## Stage 4. Claim normalisation

```typescript
extractClaims(source: ConnectorManifest, goal: Goal): Promise<Claim[]>
applyAuthority(claims: Claim[], registry: ConnectorRegistry): Claim[]
```

Extraction mode comes from the manifest: `schema` is deterministic mapping, `llm` uses the referenced prompt.

`applyAuthority` is pure and reads only the registry. **Non-authoritative claims are retained, never dropped** — they contribute zero confidence and supply mechanism detail that makes findings actionable.

Every claim carries `observedAt` and `artifactType`. Both drive decay in stage 7.

## Stage 5. Coverage map and dispatch

```typescript
mapCoverage(goal, tree, claims, registry): CoverageMap
dispatch(gaps: CoverageGap[], registry): Promise<Claim[]>
```

The goal defines required questions. The corpus answers some. For the rest:

| Gap type | Meaning | Action |
|---|---|---|
| **Analysis gap** | Data exists, nobody cut it that way | Dispatch. Minutes. Invisible to the user. |
| **Data gap** | Instrumentation does not exist | Cannot resolve. Becomes a data request in the output. |

**Only data gaps require anything from the user.** Analysis gaps are filled silently.

Two guards: cap dispatched analyses per run (default 4), and only dispatch when the missing analysis could plausibly move something into or out of the recommendations. If the coverage map shows more than the cap, that is itself a finding.

Dispatched analyses are named in the plan with expected latency.

## Stage 6. Substrate and sweeps

```typescript
buildTree(goal, archetype, businessModel): Promise<DriverTree>
buildSubstrate(tree, claims, dims): Promise<Cell[]>
runSweeps(cells, claims, goal): Promise<SweepHit[]>
```

Substrate is **assembled from existing claims first**, and only queried from raw data where claims are absent. Under v6 the corpus is primary.

Tree validation, deterministic: children reconstruct the parent within tolerance, siblings mutually exclusive, depth 3 to 4, every leaf measurable or explicitly `unknown`.

**Two sweeps, not three.**

```typescript
// STRUCTURAL SWEEP — reads the substrate
// Absorbs what earlier drafts called Scan A and Scan C; they were the same formula.
opportunity = (frontier(cell) - cell.value) × cell.population × valuePerUnit
where frontier = max(bestPeerSegment, ownHistoricalBest, corpusBenchmark)

// MANDATORY: confound check before emitting.
// If population composition differs from the frontier cell beyond threshold on
// any visible dimension, emit with blockers: ['confound_unchecked'] and cap
// confidence at 'low'.

// CORPUS SWEEP — reads everything that is not a number
// Scoped to: populations carrying the top 50% of the goal metric,
//   PLUS every segment excluded from the structural sweep for inadequate sample.
// The second half is why this sweep exists. High-value segments are usually
// small segments, and small segments are exactly what the adequacy floor excludes,
// so the structural sweep is blind precisely where concentration is highest.
```

Union the hits, **deduplicate by `mechanism`, not by wording**, and tag `surfacedBy`.

```typescript
// I1 enforcement. Add a type-level guard or lint rule.
// scoreImpact must not be able to read this field.
hit.surfacedBy  // read ONLY by scoreConfidence
```

## Stage 7. Adjudication

```typescript
clusterClaims(claims): Promise<Cluster[]>     // LLM + vector, by subject not source
adjudicate(cluster): Adjudication              // deterministic
```

```typescript
function adjudicate(cluster: Cluster): Adjudication {
  const auth = cluster.claims.filter(c => c.authoritative);
  const conflicts = findConflicts(auth);   // same type, opposite direction

  if (conflicts.length > 0)
    return { verdict: 'conflict', priority: 'top', conflicts };
    // An authoritative conflict is a FINDING. Never average it away.
    // Two authoritative sources disagreeing means the model of the business
    // is wrong somewhere, which is worth more than either claim.

  if (auth.length === 1)
    return { verdict: 'single_authoritative', claims: auth };
    // Stands at full weight. This is the quiet-high-value guard.

  return { verdict: 'corroborated', bonus: corroborationBonus(auth) };
}
```

## Stage 8. Lever generation and matching

```typescript
generateLevers(finding, context): Promise<Lever[]>       // UNCONSTRAINED
matchLibrary(finding, library): Promise<MatchedLever[]>
assessFeasibility(levers, profile): FeasibilityVerdict[] // AFTER generation
```

**Generation runs free.** The prompt is explicitly told not to filter for buildability, and to produce candidates across every `LeverType`. Target 20 to 40. Constraining generation narrows the solution space to features, when the answer is frequently an email, a rollout, a monitor, or a packaging decision.

**Feasibility is assessed afterward and nothing is deleted.** Unbuildable-this-horizon items move to the rejection ledger with a reactivation trigger. Achievable recommendations lead; unbuildable-but-valuable items get their own section framed as capability gaps.

**Always run the unshipped-winner check** when the library or experiment history is non-empty:

```typescript
// Join experiment results against feature flag state. In most companies these
// live in different systems and have never been joined. Look for:
//   - won && rolloutCoverage < 1.0        → unshipped winner
//   - won && rolloutCoverage === 0        → SILENTLY REVERTED. Highest priority.
//   - known_fails && reactivationConditionMet → resurface WITH its history
```

The silent-revert case is real and nothing detects it today. Also emit a standing recommendation to add a flag-versus-experiment audit.

Library empty? Skip all of this silently (I6).

## Stage 9. Scoring

Fully deterministic. Highest test coverage in the repo.

```typescript
function scoreImpact(f: Finding, goal: Goal): Impact {
  const affected = intersect(f.population, goal.population);
  return {
    value: affected.size * f.movableGap * goal.valuePerUnit,
    // f.surfacedBy is deliberately NOT in scope here. See I1.
    ...
  };
}

function scoreConfidence(f: Finding, m: MatchedLever | null, trust: TrustProfile): Confidence {
  // --- problem leg ---
  const strongest  = Math.max(...f.claims.map(c => STRENGTH_SCORE[c.strength]));
  const authority  = f.claims.some(c => c.authoritative) ? 1.0 : 0.4;
  const sample     = mean(f.cells.map(c => c.sampleAdequate ? 1 : 0.3));
  const coverage   = f.claims.reduce((s,c) => s + (c.populationValue ?? 0), 0) / goalPopulationValue;
  const recency    = mean(f.claims.map(c => decay(c.observedAt, DECAY_HALFLIFE_DAYS[c.type])));
  const corrob     = Math.min(0.15, 0.05 * (independentAuthoritativeClasses(f) - 1));
  const confound   = f.blockers.includes('confound_unchecked') ? 0.5 : 1.0;

  const problem = clamp01(
    (strongest*0.35 + authority*0.20 + sample*0.15 + coverage*0.20 + recency*0.10)
    * confound + corrob
  );

  // --- solution leg ---
  const solution = m ? SOLUTION_BASE[classify(m)] * stalenessMultiplier(m) : 0.40;

  // --- combine, then name the weaker leg ---
  const score = Math.min(problem, solution) * 0.7 + ((problem + solution) / 2) * 0.3;
  const weakestLeg = problem <= solution ? 'problem' : 'solution';

  return { band: band(score), score, weakestLeg, weakestLegReason: explain(weakestLeg, f, m), ... };
}

const SOLUTION_BASE = {
  known_works_full: 0.90, known_works_partial: 0.60,
  unknown_with_prior: 0.40, unknown_no_prior: 0.25, known_fails_reactivated: 0.35,
};

const band = (s: number) => s >= 0.75 ? 'high' : s >= 0.50 ? 'medium' : 'low';

function triage(problem: number, solution: number) {
  const p = problem >= 0.60, s = solution >= 0.60;
  if (p && s)  return 'ship';
  if (p && !s) return 'pilot';
  if (!p && s) return 'probe_problem';
  return 'park';
}

function estimateEffort(lever: Lever, velocity: VelocityHistory) {
  const comparable = velocity.projects.filter(p => p.surface === lever.surface);
  if (comparable.length < 3)
    return { estimate: null, derivation: 'insufficient comparable history' };  // I7
  return {
    estimate: median(comparable.map(p => p.actualWeeks)),
    derivation: `median of ${comparable.length} prior projects on ${lever.surface}: `
              + comparable.map(p => p.actualWeeks).join(', '),
  };
}
```

**Overlap discount at portfolio assembly.** Levers acting on the same population in the same window double count. Compute pairwise population and window overlap, deduct, and **report the discount as its own line item.** Flag it as an assumed parameter (I8).

**Absorption capacity** is a hard constraint, not a weight: cap changes per surface, per team, per customer-facing touchpoint.

## Stage 10. Depth tiering and prioritisation

Scoring says how big and how sure. It does not say what to do first. This stage is the decision point, and it is deliberately separate from Stage 9 so that ordering can never contaminate sizing.

**I10. Prioritisation never mutates impact or confidence.** It reads them and orders by them. `scoreImpact` and `scoreConfidence` outputs are frozen before this stage runs and are byte-identical whether prioritisation runs or not. This is I1's logic applied one layer up: the reason to do something first must not change how big we said it was.

### 10a. Depth tiering

Twenty-five candidates do not need twenty-five diagnoses. Split them, deterministically, by screening score.

```typescript
const screen = (c: Candidate) => c.impact.value * c.confidence.score;

function tier(candidates: Candidate[], cap: DepthCap): Tiered {
  const ranked = candidates.sort((a,b) => screen(b) - screen(a));
  return {
    deep:    ranked.slice(0, cap.deep),      // default 5, hard max 8
    shallow: ranked.slice(cap.deep),         // everything else, ledger only
  };
}
```

**Deep** candidates get full diagnosis: mechanism, evidence chain, effort derivation, prioritisation score, falsifiers.
**Shallow** candidates get a ledger entry: name, one-line reason it ranked where it did, screening inputs. They are visible, not hidden, and they are expandable on request (10c).

The cap exists because depth is the expensive part and because a report presenting twenty-five equally-weighted options is not a decision aid. **Five deep is the default.** Raise it only when the top of the distribution is genuinely flat.

**A shallow candidate is never silently dropped.** Every candidate that entered Stage 10 appears somewhere in the output.

### 10b. Prioritisation

Applies to the deep set only.

```typescript
interface PrioritisationFramework {
  id: string;
  source: 'company_defined' | 'default_rice';
  criteria: Criterion[];
  statedAt: string | null;              // where it was found, if company_defined
}

interface Criterion {
  key: string;                          // 'reach' | 'impact' | 'confidence' | 'effort' | custom
  weight: number;
  direction: 'higher_better' | 'lower_better';
  boundTo: string | null;               // which computed field supplies it
}
```

**Framework selection, in order:**

1. **The company's own criteria**, if `company_context` carries a prioritisation rubric, a documented scoring model, or a stated ordering principle. Adopted the same way a metric definition is adopted: read it, state it, let them change it. Never paraphrased into something tidier.
2. **RICE by default**, when nothing is found.

RICE binds to fields that already exist, and this binding is the whole trick. Nothing new is estimated.

| RICE term | Bound to | Note |
|---|---|---|
| Reach | `intersect(finding.population, goal.population).size` | Already computed in Stage 9 |
| Impact | `finding.movableGap * goal.valuePerUnit` | Already computed in Stage 9 |
| Confidence | `confidence.score` | Rendered as its band, never as a decimal |
| Effort | `estimateEffort(lever, velocity).estimate` | May be `null` under I7 |

```typescript
function prioritise(c: Candidate, fw: PrioritisationFramework): Priority {
  const effort = c.effort.estimate;
  if (effort === null)                                        // I7
    return { rank: null, unrankable: 'effort_underivable',
             derivation: c.effort.derivation };
  const score = (c.reach * c.impactPerUnit * c.confidence.score) / effort;
  return { rank: null, score, inputs: {...}, framework: fw.id };
}
```

**Effort that cannot be derived does not get invented.** An item with no comparable history returns `unrankable`, is listed in its own short section beneath the ranking, and says why. Fabricating an effort number to complete a RICE score would launder a guess into a decision, which is precisely the failure I7 exists to stop.

**The output shows the inputs, not just the ordering.** A rank the reader cannot interrogate is an oracle. Every ranked item renders its four terms and the arithmetic.

**Weight changes are cheap and must be.** Because prioritisation reads frozen scores, re-ordering under different weights requires no recomputation upstream. The output carries enough to re-rank client side, and a user asking "what if effort mattered more" gets an answer immediately rather than a new run.

### 10c. Expanding a shallow candidate

The ledger is not a graveyard. A reader who wants to know why number fourteen placed there can ask, and the system does real work rather than restating the one-liner.

```typescript
deepenCandidate(runId: string, candidateId: string): DeepDive
```

Re-enters the pipeline for that candidate alone: pulls its full claim set, runs adjudication and lever matching if they were skipped, derives effort, and returns the same diagnosis a deep candidate would have received. It does **not** re-rank the run, and it does **not** promote the candidate. Ordering is a property of the run, and quietly reshuffling it because someone asked a question would make the report unstable.

Each `LedgerEntry` therefore retains what is needed to resume: `claimIds`, screening inputs, the stage it stopped at, and the reason it stopped.

### 10d. The framework is approved in plan mode

**The prioritisation criteria are proposed at Stage 3 and approved with the plan, not chosen at the end.** A plan says what will be investigated. It must also say how the results will be ordered, because a reader who sees the criteria only after seeing the ranking cannot tell whether the criteria were chosen to fit the ranking.

The plan states: the framework and its source, the depth cap, and the fact that everything below the cap will still be listed. Changing the framework after results exist is permitted, logged, and shown as a re-ordering with both orders visible.

---

## Stage 11. Output assembly

```typescript
assembleOutput(findings, goal, ledger): CrucibleOutput
```

```typescript
interface CrucibleOutput {
  tldr: { headline: string; topThree: RankedItem[]; total: string; ruledOut: string };
  diagnosis: DiagnosisSection[];        // carries chart specs, quotes, evidence tables
  recommendations: RankedItem[];        // the deep set, in prioritised order
                                        // each carries >=1 EvidenceArtifact (11b)
  prioritisation: {
    framework: PrioritisationFramework; // stated, with its source
    table: PriorityRow[];               // one row per deep item, all inputs visible
    unrankable: UnrankableItem[];       // effort underivable (I7), with reason
    sensitivity: string | null;         // what re-orders under plausible weight changes
  };
  byImpact: RankedItem[];
  byConfidence: RankedItem[];
  inBoth: string[];
  gapToTarget: { reachable: number; target: number; shortfallReason: string | null };
  committedWorkInteractions: Interaction[];
  unlockingPrerequisites: Prerequisite[];
  considered: LedgerEntry[];            // the shallow tier plus rejects, expandable (10c)
  dataRequests: DataRequest[];          // max 2. Only when it changes the answer.
  assumptionsToCheck: AssumedParam[];   // I8
  falsifiers: string[];
}
```

**Structure, in order.** TL;DR first, answering the question asked with the top three and their sizes. Then diagnosis, the analysis that produced the opportunities. Then the recommendations themselves, in depth. Then the prioritisation table, which is the decision point: the framework, the inputs, the order, and what re-orders under different weights. Then gap to target. Then both raw rankings for anyone who wants to re-cut it. Then considered-and-not-prioritised, listed with reasons and marked expandable. Then assumptions to cross-check, last.

**The reader has two exits and both are supported.** One reader wants the why and reads diagnosis. Another accepts the findings and jumps to the prioritisation table to decide what to start Monday. Neither should have to read the other's section to get what they came for.

**Every statement passes the causal lint before it leaves the service:**

```typescript
const BANNED = ['causes','drives','leads to','results in','because of','due to'];
export function lintClaim(text: string, strength: EvidenceStrength) {
  if (strength === 'causally_tested') return { ok: true };
  const hit = BANNED.find(v => text.toLowerCase().includes(v));
  return hit ? { ok: false, violation: hit } : { ok: true };
}
```

Lint failure is a hard error. Regenerate or fail the item.

## Stage 11b. Evidence rendering

**The single most common way a good analysis fails is by arriving as assertion.** A reader handed "$492M, medium confidence" has no way to evaluate it and only two available responses: accept it on authority or discount it. Neither is the response you want. The evidence layer exists so a sceptical reader can check the reasoning rather than trust the conclusion, and it is not decoration on top of the analysis. It is the part that makes the analysis usable by someone who was not in the room.

**Rule: every deep recommendation carries at least one rendered evidence artifact.** A chart, an evidence table, or an attributed quote. A deep recommendation that renders as prose alone fails acceptance. If no artifact can be produced, that is a signal the finding is thinner than its score suggests, and it should be checked rather than shipped.

### Evidence types and what each is for

```typescript
type EvidenceArtifact = ChartSpec | EvidenceTable | Quote;

interface ChartSpec {
  kind: 'distribution' | 'comparison' | 'funnel' | 'timeline' | 'buildup' | 'scatter';
  claimIds: string[];              // every series traces to claims. I6.
  title: string;
  subtitle: string | null;         // states what the reader should see
  series: Series[];
  annotations: Annotation[];       // the point of the chart, marked on it
  nullPolicy: 'gap';               // unmeasured renders as a gap, never as 0. I3.
}

interface Quote {
  text: string;                    // VERBATIM. Never paraphrased, never composited.
  sourceId: string;
  sourceType: string;              // 'research_interview' | 'support_ticket' | 'review'
  attribution: string;             // segment and role, never an identity
  collectedAt: string;
  populationScope: PopulationScope;
  illustrates: string;             // the mechanism it illustrates
  representativeOf: string | null; // null unless sampling supports it
}

interface EvidenceTable {
  claimIds: string[];
  columns: Column[];
  rows: Row[];
  highlight: { row: number; col: number; reason: string }[];
}
```

### Charts

**A chart earns its place when the shape carries the argument.** Distributions where the shape is the finding, comparisons where the gap is the finding, funnels where the drop is the finding, timelines where the timing is the finding, build-ups where the composition is the finding. Two numbers are a sentence, not a chart.

Four requirements, all enforced:

1. **Every series traces to `claimIds`.** A chart is a rendering of claims already adjudicated, never a separate assertion. A number that appears in a chart and nowhere in the claim graph is a hard error.
2. **The annotation states the point.** An unannotated chart makes the reader find the argument themselves, and half of them will find a different one. Mark the thing you want seen.
3. **Missing data renders as a visible gap, never as zero.** I3 applies to pixels as much as to numbers. A dip to zero in a line chart is a claim that the value was zero.
4. **Axes start at zero for magnitude comparisons.** A truncated axis manufactures a difference, which is the visual form of the causal-language problem I5 exists to prevent.

### Quotes

**Quotes are how mechanism becomes legible.** A retention number tells you people left. A quote tells you what it felt like to be the person leaving, and that is what lets a PM design a fix rather than a metric.

**Verbatim or not at all.** Never tidied, never composited from several speakers, never trimmed in a way that changes meaning. A cleaned-up quote is a fabrication wearing quotation marks.

**Attribution is by segment and role, never by identity.** "Solo advertiser, 4 months tenure" is attribution. A name is a privacy problem the customer did not ask for.

**Authority bound, and this is the one that gets violated.** Under I4, a quote is authoritative for mechanism and never for magnitude. Three interviewees describing the budget field as advice is evidence about how it is read. It is not evidence that 41% of advertisers read it that way. **The quote explains the shape the telemetry measured; it never sizes it.** Where a quote and a number appear together, the number carries the size and the quote carries the why, and the text must not blur which is doing which.

**Selection is disclosed.** State how many were reviewed and how these were chosen. "Three of 340 interviews, selected as the clearest statements of the dominant theme" is honest. Three quotes presented as though they were the corpus is not, and a reader who later sees the full set will discount everything else in the report.

### The evidence chain

Every deep recommendation renders a short chain the reader can walk:

```
what we observed  →  in which population  →  at what strength  →  what it implies  →  what would falsify it
```

**Rendered explicitly, not implied by paragraph order.** This is the structure that makes a finding checkable, and it is also the structure that makes the difference between the corroboration a reader sees and the impact score visible: the chain shows what supports the finding, while the size came from population and gap alone (I1).

### Analysis narrative

Diagnosis prose states **how the finding was reached**, not only what it is. Which sources were read, what each showed alone, what only appeared when they were read together, and what was ruled out along the way. The reader should be able to reconstruct the inference.

The causal lint (I5) applies to every word of it. Describing how two sources combine is not licence to assert that one caused the other.

**Never render confidence decimals.**

---


---

# 6. Persistent stores

| Store | Contents | Written by |
|---|---|---|
| `goal_definitions` | Locked goal definitions, source refs, hashes, conflicts, provenance | Stage 0 (never mutated, only superseded) |
| `connector_manifests` | Per-connector capability declarations | Onboarding (4.3) |
| `trust_profile` | Class weights, source reliability, provenance | Plan edits, outcomes |
| `lever_library` | Levers with state, preconditions, effects, rollout coverage | Outcome capture |
| `rejection_ledger` | Candidate, estimate, evidence, reason, reactivation condition | Stage 10, **seeded from tracker history at onboarding (4.5)** |
| `company_context` | Vocabulary, metric definitions, org structure | Plan `teach` moves |
| `playbook` | YAML config, resolved by inheritance | PM edits |
| `outcomes` | What shipped, what happened, which source predicted it | Post-ship |

Playbook inheritance: `base` → `business_model` → `archetype` → `company` → `goal_instance`.

**Start writing `outcomes` in Milestone 2 even though nothing reads it until Milestone 4.** The value only appears after six to twelve months of runs.

---

# 7. LLM call sites

Exactly ten. Everything else is deterministic. Each has a versioned prompt file and a golden-output test.

| # | Site | Returns | Never returns |
|---|---|---|---|
| 0a | Metric registry matching (Stage 0) | Candidate metric matches with confidence | A selection, a definition |
| 0b | Corpus definition extraction (Stage 0) | Verbatim definitional statements | A paraphrase, a synthesised definition |
| 1 | Goal parsing | Target, horizon, constraints, scope | Any score, the metric definition |
| 2 | Claim extraction | `Claim[]` | Authority, confidence |
| 3 | Driver tree generation | `DriverTree` | Sizing |
| 4 | Claim clustering | Cluster assignments | Adjudication verdict |
| 5 | Lever generation | `Lever[]`, unconstrained | Impact, rank |
| 6 | Precondition matching | `full` / `partial` / `none` | A confidence number |
| 7 | Onboarding classification | Proposed `ConnectorManifest` | Final authority (validated) |
| 8 | Narrative rendering | Prose around fixed numbers | Any number not passed in |

**Call site 2 is the highest-variance step and everything depends on it.** Build the eval harness for it first (section 10).

**Sites 0a and 0b are search operations, not definition operations (I9).** Neither may originate meaning. 0b must return the source string byte-identical; its golden set must include near-misses, meaning documents that quote a metric without defining it, since accepting one of those is the likeliest route to an invented definition.

---

# 8. Testing and acceptance

## Unit tests, deterministic paths

```
grounding    MDE against known statistical fixtures
grounding    Gini on uniform, skewed, single-holder distributions
grounding    Trajectory decomposition sums to the observed change
registry     resolve() returns only authoritative sources when flagged
registry     self-selected sources are REJECTED as magnitude-authoritative
registry     manifest validation rejects intersecting authority sets
substrate    sizeCells never coerces null to 0
substrate    inadequate cells set excludedFromSweep before any sweep runs
sweeps       structural sweep emits confound blocker beyond composition threshold
sweeps       corpus sweep includes every segment excluded for inadequate sample
adjudicate   'conflict' on opposing authoritative claims
adjudicate   single authoritative claim retains full weight
scoring      scoreImpact output is byte-identical when surfacedBy is mutated   ← flagship
scoring      decay uses the per-claim-type half-life, not a global one
scoring      triage across all four quadrants at boundary 0.59 / 0.60
scoring      estimateEffort returns null with a reason when history < 3
output       causal lint blocks each banned verb at each non-tested strength
output       no rendered string contains a confidence decimal
output       every assumed parameter appears in assumptionsToCheck
```

**The `scoreImpact` invariance test is the most important in the suite.** It is the executable form of I1. Without it, a future refactor will helpfully add a cruciblence bonus to impact and silently reintroduce the failure this system exists to prevent.

## Claim extraction eval harness

Build this before anything else in Milestone 2.

```
Fixture: 200 hand-labelled source documents across every artifact type.
Measure per run: precision, recall, strength-assignment accuracy,
                 population-scope accuracy, observedAt accuracy.
Gate: no prompt change ships if recall drops or strength accuracy falls below 0.85.
```

## Integration fixtures

```
FIXTURE  b2b_transactional   Rich transactional, thin qualitative, thin experiments
FIXTURE  developer_selfserve Rich telemetry, rich community, uninformative tickets
FIXTURE  consumer_scale      Rich behavioural, 140 experiments, loud reviews
FIXTURE  enterprise_low_n    84 accounts, no telemetry, six years of sales notes
FIXTURE  marketplace         Two-sided, one side silent

TEST  A quiet finding in a high-value segment, visible in ONE source, appears
      in the top three by impact.                              ← flagship behaviour
TEST  A loud finding in a low-value segment, visible in five sources, ranks
      below it on impact and above it on confidence.
TEST  enterprise_low_n produces detectable=false in the plan and switches to
      account-level measurement.
TEST  consumer_scale surfaces at least one unshipped winner and one silent revert.
TEST  marketplace produces a supply/demand split in the driver tree.
TEST  A known_fails lever with mechanism_invalid never appears in either list.
TEST  Empty lever library produces output with no mention of a lever library.
TEST  Numeric output is deterministic across runs. Golden-file it.
```

## Acceptance criteria

1. `POST /goal` returns a plan with all blocks populated in under 90 seconds.
2. Plan text is under 600 words.
3. A `teach` edit persists and applies on the next run for that company.
4. Every `RankedItem` carries a confidence band, a weakest-leg sentence, and either an effort derivation or an explicit null with a reason.
5. No rendered statement contains a banned causal verb unless causally tested.
6. Every finding traces to at least one claim, and every claim retains its `raw`.
7. The rejection ledger from run N is checked at the start of run N+1 and reactivated entries surface.
8. Adding a connector requires zero changes to files under `src/stages/`.
9. No run reaches Stage 1 with an unlocked goal definition, and two conflicting registry definitions always surface as a user-facing conflict rather than a selection (I9).
10. A second run against a locked goal asks nothing and says nothing about the definition; a changed source definition is caught by hash and surfaced with a diff.
11. Every degradation produces a `CoverageNote` that renders; no degradation is silent.
12. A semantic empty result never raises an error and never becomes 0.
13. Fallback ladders are walked deterministically; no LLM call site receives an error payload or selects a recovery path.
14. `scoreImpact` and `scoreConfidence` outputs are byte-identical with Stage 10 enabled and disabled (I10), verified by a fixture test that runs both ways.
15. An item whose effort is underivable appears in `unrankable` with its derivation string, never with a computed priority score.
16. Every candidate entering Stage 10 appears in the output, in either `recommendations` or `considered`. Count in equals count out.
17. `deepenCandidate` returns a full diagnosis for a shallow item without changing the order of the run it belongs to.
18. The prioritisation framework named in the approved plan is the one used in the output, or the change is logged and both orders are shown.
19. Every deep recommendation carries at least one `EvidenceArtifact`. A deep recommendation rendering as prose alone fails.
20. Every chart series resolves to `claimIds` present in the claim graph. A charted number with no backing claim is a hard error.
21. Quotes are byte-identical to source text, carry segment-level attribution, and never appear as support for a magnitude claim (I4).
22. Charts render unmeasured values as visible gaps, never as zero (I3), and magnitude comparisons use zero-based axes.

Criterion 8 is the extensibility test. Enforce it in CI with a dependency check.

---

# 9. Build order

**Milestone 1, plan mode.** Stage 0 goal resolution, which gates everything and is cheap to build. Then stages 1 to 3. Connector registry and manifest framework. One archetype (revenue), two business models. Causal lint, because it is cheap and it is what earns trust in the first demo. Manual gates.

The goal of M1 is not a good plan. It is finding out whether a PM reads a proposed plan, edits it, and feels ownership of the result. **Measure edit rate.**

**Milestone 2, findings.** Claim extraction plus its eval harness first. Then stages 4 to 7. Authority matrix and manifest validation. Coverage map and dispatch. Both sweeps. Adjudication. Output is findings with impact and confidence, no levers.

**Milestone 3, decisions.** Stages 8 to 10. Lever library seeded from design partner history. Unshipped-winner check. Triage, both rankings, rejection ledger, overlap discount, data requests. Charts and quote rendering. Start writing `outcomes`.

**Milestone 4, compounding.** Outcome write-back into library, trust profile, and ledger. Reactivation surfacing at stage 3. Cross-customer priors. Confidence calibration measurement.

---

# 10. The riskiest assumptions in this design

State these to whoever funds the work.

**That a PM engages with the plan rather than skipping it.** Plan mode only works if it is read. If PMs click approve without reading, we have added a step and gained nothing, and will still be blamed for a wrong framing. Instrument this first.

**That companies accept a driver tree they did not build.** Everything downstream inherits its structure. Put a generated tree in front of five design-partner PMs before building any scoring. Editing is the win condition. Dismissal means the archetype library must be rebuilt from real operating models.

**That claim extraction is accurate enough.** Every finding is downstream of call site 2. If extraction assigns the wrong strength or the wrong population, the scoring is precise nonsense. The eval harness is not optional.
