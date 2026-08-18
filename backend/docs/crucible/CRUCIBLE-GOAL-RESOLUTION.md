# Crucible: Goal Resolution

**Status:** Specification. Slots into CRUCIBLE-SPEC.md as Stage 0, ahead of Intake.
**Supersedes:** Part 2 of crucible-enterprise-readiness.md, which proposed inferring a definition when none existed. That approach is withdrawn.

---

# 1. The principle

**Crucible never invents the definition of a goal.**

Everything downstream is sized, ranked, and argued against the goal's definition. A wrong definition does not produce a slightly wrong answer, it produces a fully coherent answer to the wrong question, which is the worst failure mode this system has because nothing later in the pipeline can detect it.

So the rule is narrow and strict:

| Situation | What Crucible does |
|---|---|
| The goal is already defined in the company's product or systems | Adopt that definition exactly. Do not restate it, improve it, or normalise it. |
| The goal is not defined, or is defined more than one way | Ask the user to define it concretely. Do not proceed on a guess. |
| Either way | Confirm the resolved definition with the user, grounded in real numbers, before any analysis runs. |
| Once confirmed | Lock it, persist it, and reuse it on every subsequent run without asking again. |

The user tells Crucible where to look. Crucible does the concrete work of finding the actual signal, then proposes it back and aligns. Crucible does not supply meaning the company has not supplied.

---

# 2. Invariant

**I9. The goal definition is adopted or elicited, never inferred.**

A `GoalDefinition` may only reach `status: 'locked'` through one of two paths: adoption of an existing definition found in a connected system, or explicit user confirmation of a definition they supplied or approved. No LLM output may set `status` to `locked`. No code path may proceed past Stage 0 with an unlocked definition.

**Test:** `resolveGoal()` returns `status: 'locked'` only when `confirmedByUserAt !== null`. Attempting to run Stage 1 with an unlocked definition is a hard error, not a warning.

**Why it is an invariant and not a preference:** every other invariant protects the quality of the answer. This one protects the identity of the question. It has to sit above the others.

---

# 3. Data model

```typescript
interface GoalDefinition {
  id: string;
  rawGoalText: string;              // exactly what the user typed or said

  // Resolution
  status: 'unresolved' | 'candidate' | 'locked';
  origin: 'adopted' | 'elicited';   // never 'inferred'
  metricName: string;               // the company's name for it, not ours

  // Where it lives
  sourceRef: MetricSourceRef | null;
  definitionText: string;           // the company's own words, verbatim where possible
  definitionSourceRef: string | null; // where that text came from

  // Concrete grounding, computed before confirmation
  grounding: GoalGrounding | null;

  // Target
  direction: 'increase' | 'decrease';
  targetValue: number | null;
  targetKind: 'absolute' | 'delta' | 'percent_change' | 'threshold' | 'none';
  horizonWeeks: number | null;
  currency: GoalCurrency;

  // Provenance and lifecycle
  confirmedByUserAt: string | null;
  confirmedByUserId: string | null;
  definitionHash: string;           // hash of definitionText + sourceRef config
  supersedes: string | null;        // prior GoalDefinition.id
  conflictsFound: DefinitionConflict[];
}

interface GoalGrounding {
  currentValue: number;
  asOf: string;
  populationSize: number;
  populationDescription: string;    // who is counted, in their terms
  updateFrequency: string;
  historyWeeks: number;
  variance: number;
  trailingChange: { window: string; delta: number } | null;
}

interface DefinitionConflict {
  sourceRefA: string;
  definitionA: string;
  sourceRefB: string;
  definitionB: string;
  divergence: string;               // plain description of how they differ
  materialityEstimate: number | null; // how much the answer would change
}

interface MetricSourceRef {
  connectorId: string;
  systemMetricId: string;
  systemMetricName: string;
  ownerTeam: string | null;
  lastModified: string | null;
}
```

**Persisted in a new store, `goal_definitions`.** Keyed by company plus normalised metric name. Read at the start of every run.

---

# 4. Resolution flow

Five steps, run in order. Steps 1 to 3 are machine work. Step 4 is the only place a question can be asked. Step 5 is mandatory in every case.

```
        ┌─────────────────────────────────────────┐
        │  Step 1. Lookup in goal_definitions     │
        └───────────────┬─────────────────────────┘
                        │ hit and hash unchanged
                        ├────────────────────► LOCKED, skip to Stage 1
                        │ miss, or hash changed
                        ▼
        ┌─────────────────────────────────────────┐
        │  Step 2. Search connected systems       │
        └───────────────┬─────────────────────────┘
              ┌─────────┼──────────┬───────────────┐
     exactly one     multiple      none
              │         │              │
              ▼         ▼              ▼
         ADOPT     CONFLICT        Step 3. Search corpus
              │         │              │
              │         │       ┌──────┴──────┐
              │         │    found         not found
              │         │       │              │
              │         │       ▼              ▼
              │         │    ADOPT        Step 4. ASK
              │         │  (as candidate)     │
              └─────────┴───────┬─────────────┘
                                ▼
        ┌─────────────────────────────────────────┐
        │  Step 5. Ground and confirm  (mandatory) │
        └───────────────┬─────────────────────────┘
                        ▼
                     LOCKED
```

## Step 1. Lookup

Normalise the goal text to a metric key and look it up in `goal_definitions`.

On a hit, recompute `definitionHash` from the live source configuration. If unchanged, the definition is locked and the run proceeds immediately with no questions. **This is the common case after the first run and it must be silent.**

If the hash changed, the definition drifted upstream. Route to Step 5 with a change notice, described in Section 7.

## Step 2. Search connected systems

Query every connector that declares `capabilities.metricRegistry` for a metric matching the goal. Match on name, then on alias, then on LLM-assisted semantic match against the registry, which is a search operation and not a definition operation.

Three outcomes.

**Exactly one match.** Adopt it. Copy the definition text verbatim from the source. Do not paraphrase, do not tidy the wording, do not convert units. If their analytics tool says activation is completing a first project within seven days of signup, that string is the definition. Set `origin: 'adopted'`.

**More than one match, and they disagree.** This is a conflict, not a tie to break. Record every candidate in `conflictsFound` with a plain description of how they differ and, where computable, how much the answer would move under each. Then route to Step 4 and ask which one governs. **Crucible does not pick.** At an enterprise this case is common and picking silently is how you produce an answer that one division agrees with and another rejects on sight.

**No match.** Continue to Step 3.

## Step 3. Search the corpus

The metric may not be in a registry but may be defined in the company's own written work: a metrics doc, an analysis appendix, a QBR that states the definition, a team charter.

Search finished analyses for an explicit definitional statement. Accept only statements that actually define, meaning they state what is counted, over what population, over what window. A number quoted without a definition is not a definition.

On a find, adopt it as a **candidate**, not as locked, because a definition written in a document has weaker standing than one implemented in a system. The confirmation in Step 5 does the work of promoting it.

On no find, continue to Step 4.

## Step 4. Ask

The only place a question is permitted. Requirements are in Section 5.

## Step 5. Ground and confirm

**Mandatory in every path, including clean adoption.** Even when exactly one definition was found and it is unambiguous, the user confirms before analysis runs.

Before confirming, pull the concrete grounding: the current value, the population size and description, how often it updates, how much history exists, and the recent movement. Confirmation is done against real numbers, never against an abstract description.

**State the calculation in the same step (section 6).** Show the definition found in their systems in one plain sentence and let them correct it. No separate methodology round.

On confirmation, set `status: 'locked'`, stamp `confirmedByUserAt` and `confirmedByUserId`, persist to `goal_definitions`, and proceed to Stage 1.

---

# 5. How Crucible asks

Asking is not a failure state, it is a normal step, and the quality of the ask is what makes it feel competent rather than helpless.

**Four requirements. All mandatory.**

**1. Show the search before showing the gap.** Never open with what you do not know. Open with what you looked at. The user needs to see that the question is arriving after effort, not instead of it.

**2. Bring concrete candidates with live numbers.** Never ask an open question. Every candidate carries its current value, its population, its freshness, and where it lives. The user should be able to answer by pointing rather than by composing.

**3. Name the consequence of the choice.** State what changes about the analysis under each option. This is what turns a form field into a decision, and it is the part that earns the user's attention.

**4. Leave the door open for a definition you did not find.** Always close with an explicit invitation to supply a definition that is not on the list, because at an enterprise the real definition frequently lives in a team's head.

## Template: no definition found

```
"Reduce user complaints by 8 percent."

I looked for a complaints metric in Amplitude, Zendesk, the App Store
Connect feed, and your last four QBR decks. There isn't one definition
of "complaints" that these agree on, so I want to pin it down before I
size anything.

What I can see:

  · App Store reviews at 3 stars or below
    Currently 3.5 avg across 4,180 reviews in the last 90 days.
    Updates daily. Two years of history.

  · Zendesk tickets tagged complaint or escalation
    2,340 in the last 90 days, from 1,890 accounts.
    Updates hourly. Tag applied manually, so coverage is uneven.

  · In-app feedback submissions with negative sentiment
    11,200 in the last 90 days. No sentiment field, so this would
    need classifying before it's usable.

These behave differently. App Store reviews skew to people who churned or
nearly did, so moving that number is mostly a retention story. Zendesk
tickets skew to paying accounts with a support relationship, so that's
mostly a product-defect story. They will not produce the same
recommendations.

Which one is the goal? Or if complaints means something specific
internally that I haven't found, tell me and I'll use that.
```

## Template: conflicting definitions

```
"Increase activation by 5 points."

Activation is defined two ways in your systems and they don't agree.

  · Amplitude, owned by Growth
    Completed first project within 7 days of signup.
    Currently 31.2 percent. n = 48,000 signups in the window.

  · The data warehouse metric layer, owned by Data Platform
    Any project event within 30 days of signup.
    Currently 44.6 percent. Same population.

The 13-point gap is mostly the window, not the event. The 7-day version
makes onboarding speed the story. The 30-day version makes it mostly
about re-engagement, and roughly a third of the gap to a 5-point target
would come from people who activate in week three or four.

Which one governs? I'll use it consistently from here.
```

## Template: goal is not a metric

The system still refuses to invent the meaning. It surfaces what exists and asks.

```
"Make onboarding better."

There isn't a metric called that, so I need you to point me at what
"better" means before I start.

Things your team already measures that sit in onboarding:

  · Time to first value, median 4.2 days, tracked in Amplitude
  · Week-one retention, 52 percent, tracked in the warehouse
  · Setup completion rate, 68 percent, tracked in Amplitude
  · Onboarding CSAT, 4.1 of 5, n = 340, low volume

Your onboarding retro from March used the first two together. If that's
still how you think about it, say so and I'll use both, with time to
first value as the primary. Otherwise tell me which one, or describe the
outcome you want and I'll come back with the closest measurable thing
for you to approve.
```

Note the shape: Crucible surfaces the company's own prior usage as a candidate, which is legitimate, and still does not adopt it without confirmation.

---

# 6. Calculation methodology

A metric name is not a full definition. The same name can mean different numbers: revenue can be recognised or booked, active can mean logged in or took an action. The company's own calculation is the definition, and Crucible's job is to surface it, not to reconstruct it or interrogate the user about it.

**The rule is simple. Show them the definition you found, in their clarification, and let them change it.**

- If the company's systems carry a computation for the metric, a dbt model, a metric-layer entry, a documented formula, read it, state it in one plain sentence, and present it as the definition being used.
- The user can accept it by doing nothing, or correct it in the same step where they confirm the goal.
- If no computation is found, state the common convention you are assuming for that metric, in one sentence, and let them change it.

That is the whole mechanism. No parameter-by-parameter questionnaire, no materiality thresholds, no separate methodology round. One stated definition, editable.

## What this looks like

```
Goal: increase revenue by 4M by end of Q4.

Revenue here means Net Revenue from your warehouse metric layer: recognised
monthly amounts, net of refunds, excluding internal and comped accounts.
Currently 61.2M trailing twelve months.

If that's not the revenue you're steering by, tell me and I'll use yours.
```

The definition is stated in one sentence, it came from their own system, and changing it is one reply away. If they say nothing, that is the definition.

## Persistence

Whatever definition is confirmed, adopted as found or corrected by the user, persists with the goal and is reused silently on later runs. A user correction is itself a definition and locks the same way. If the underlying computation changes at the source, that triggers the normal drift flow (section 8).

---

# 7. Confirming an adopted definition

This is the same single exchange as section 6, seen from the other side: section 6 governs what the stated definition contains, this governs how it is put to the user. There is one confirmation, not two.

When Step 2 found exactly one clean definition, the confirmation is short. It is not a question, it is a statement with an escape hatch, and it should take two seconds to clear.

```
Goal: increase activation from 31.2 to 36.2 percent by end of Q4.

Using your Amplitude definition of activation, completed first project
within 7 days of signup. Currently 31.2 percent across 48,000 signups
in the last 90 days, updated daily, 26 months of history. It has moved
between 29.8 and 33.1 over the last year.

Starting there unless you tell me otherwise.
```

**Three properties.** It states the definition in their words. It proves the metric is real by showing live numbers. And it makes the default action "do nothing," which keeps the confirmation from becoming friction on every run.

This confirmation appears once, at first lock. Subsequent runs skip it entirely.

---

# 8. Locking, reuse, and drift

## Locking

On confirmation, the definition is written to `goal_definitions` with its hash, its source reference, and its provenance. Every artifact produced by the run cites the `GoalDefinition.id`, so any number can be traced back to the definition it was computed under.

## Reuse

Every later run against the same goal reads the locked definition and proceeds silently. **No re-confirmation, no re-asking, no restating the definition in the output.** A system that re-litigates a settled question every run reads as broken.

## Drift detection

On every run, recompute `definitionHash` from the live source. Four triggers force a return to Step 5.

| Trigger | Response |
|---|---|
| Definition text changed at the source | Show the diff, ask whether to adopt the new one or continue with the locked one |
| The metric source was removed or renamed | Re-resolve from Step 2, flag the break |
| A second conflicting definition appeared | Surface the conflict, ask which governs |
| The user's stated goal text materially differs from the locked one | Treat as a new goal, do not silently reuse |

Drift notices are short and carry the consequence:

```
Your Amplitude activation definition changed on 14 July. The window
moved from 7 days to 14 days, which lifts the current reading from
31.2 to 38.4 percent.

Two prior analyses used the 7-day version. Adopt the new definition,
or keep the locked one for continuity?
```

## Superseding

Adopting a changed definition writes a new `GoalDefinition` with `supersedes` pointing at the old one. Never mutate a locked record, because prior reports were computed under it and must remain traceable.

---

# 9. Taking any goal

The mechanism above is definition-resolution, and it is indifferent to what kind of goal is being defined. This section confirms coverage across goal shapes and names what varies.

| Goal shape | Example | Resolution | What varies |
|---|---|---|---|
| Tracked metric, delta target | Increase activation 5 points | Adopt from registry | Nothing, this is the base case |
| Tracked metric, threshold target | Get app rating to 4.5 | Adopt from registry | `targetKind: 'threshold'`. Grounding must include distance to threshold and historical variance, since threshold goals are often inside noise |
| Reduction of a counted event | Reduce complaints 8 percent | Usually Step 3 or 4, since event definitions are rarely registered | Which event stream counts, and the population it is drawn from. Almost always requires an ask |
| Acquisition count | Acquire 40,000 new users | Adopt, but confirm what counts as acquired | Signup versus verified versus first-action. Materially different denominators |
| Cost or efficiency | Cut support cost per ticket 20 percent | Often lives in finance systems, not product analytics | Cross-system resolution, and the allocation rule is part of the definition |
| Duration | Halve time to first value | Adopt if registered | Both endpoints are part of the definition. Confirm the start event explicitly |
| Composite or index | Improve trust score | Rarely registered | Its components are part of the definition. If they cannot be enumerated, this is an ask |
| Ratio | Improve LTV to CAC to 3.5 | Both sides need separate resolution | Two definitions, two possible conflicts, confirm both |
| Directional, no number | Improve retention | Adopt the metric, ask for the target | Definition resolves normally. `targetKind: 'none'` triggers a separate target question in Stage 1, not Stage 0 |
| Non-metric | Make onboarding better | Never adopt | Always an ask. Surface existing measures and prior internal usage as candidates |
| Multi-metric | Grow revenue without hurting retention | Resolve each independently | Primary and constraint are separate `GoalDefinition` records. The constraint gets a floor, not a target |

**The rule that makes this general:** Crucible's job at Stage 0 is not to understand the domain, it is to determine whether a definition exists and to obtain one if it does not. That job is identical for a complaint-reduction goal and a revenue goal. Nothing in Stage 0 is archetype-specific, and no goal is refused.

---

# 10. What is explicitly not permitted

These are the failure modes this stage exists to prevent. Each is a hard error, not a warning.

**Proceeding on an unconfirmed definition.** No path past Stage 0 without `status: 'locked'`.

**Silently choosing between conflicting definitions.** Including choosing the more recently updated one, the one with more history, or the one from the higher-trust connector. Conflicts go to the user.

**Normalising a company's definition.** If their activation window is seven days and every other customer uses fourteen, it stays seven. Cross-customer priors may inform lever matching. They may never inform definition.

**Restating the definition in different words.** The confirmation shows their string. Paraphrasing introduces drift the user cannot see.

**Inventing a proxy and running on it.** A proxy may be proposed. It may not be adopted without confirmation.

**Asking an open question.** Any ask that does not carry candidates, live numbers, and the consequence of the choice fails review.

**Asking twice.** Once locked, the definition is silent until it drifts.

---

# 11. Build notes

## Where it sits

New Stage 0, ahead of Intake. `parseGoal()` in Stage 1 no longer produces the metric definition, it produces target, horizon, constraints, and scope against an already-locked definition.

## Store

New `goal_definitions` store, written at Stage 0, read at the start of every run, never mutated after lock.

## LLM call sites

Two additions to the eight in CRUCIBLE-SPEC.md, both search operations and neither definitional:

| Site | Job | Constraint |
|---|---|---|
| Metric registry matching | Match goal text to registered metrics | Returns candidates with confidence, never selects |
| Corpus definition extraction | Find explicit definitional statements in documents | Must return the source string verbatim, not a summary |

Both need golden tests. The corpus extractor's test set must include near-misses, meaning documents that quote a metric without defining it, since accepting those is the likeliest way an invented definition gets in.

## Acceptance criteria

1. A run cannot reach Stage 1 with `status !== 'locked'`
2. Two conflicting registry definitions always produce a user-facing conflict, never a selection
3. A second run against a locked goal asks nothing and shows nothing about the definition
4. A changed source definition is detected by hash and surfaced with a diff
5. Every ask in the golden set carries at least one candidate with a live value, and names a consequence
6. Adopted definition text is byte-identical to the source string
7. Non-metric goals produce an ask, never a run
8. Locked records are never mutated, only superseded

## Instrumentation

Log at every resolution: which step resolved it, whether a conflict was found, whether an ask was needed, time to confirmation, and whether the user accepted the proposed candidate or supplied their own.

**The metric that matters is the share of runs resolving at Step 1**, meaning silently from a locked definition. It should climb toward one as an account matures. If it does not, definitions are drifting or goals are being restated inconsistently, and both are worth knowing early.
