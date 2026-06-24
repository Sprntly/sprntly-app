# Evidence Brief — skill folder README

> **Read this first.** This folder is a self-contained Agent Skill. It contains everything
> needed to understand, invoke, and reproduce an *evidence brief*: the behavior spec
> (`SKILL.md`), this guide, and five worked examples (`examples/`). It is written so an
> LLM or a developer can pick it up with no other context and know **what the skill is,
> when to call it, what it needs, what it outputs, and what that output is used for.**

---

## 1. What this skill is (one line)
`evidence-brief` turns the analysis a product org already has — data science, competitive,
voice of customer, market — plus business context into **one visual brief that tells a single
data story and ends in a testable, value-driven hypothesis** for a product team.

## 2. What it produces
A self-contained **HTML document, 1–3 pages**, for a **product-team audience**, written in the
**voice of a data scientist on the team**. Fixed section order:

1. **Title** — a product-led strategic thesis (e.g. "Unlock Retention by Letting Beginners Speak"). Never a first-person/opinion line.
2. **TL;DR** — the whole story in ~5 lines.
3. **Opportunity** — one simple line.
4. **Context** — what the product is + the behavior under investigation (placed *after* TL;DR and Opportunity).
5. **Evidence** — the findings, each with the best-fit chart, sequenced as one story.
6. **Convergence** — where independent signals agree (or an honest note that they don't).
7. **Hypothesis** — value-driven (behavior change → business value), testable, with metric + guardrails, plus a "→ feeds the PRD" handoff.

No footer, no methods boilerplate, no mention of how it was produced.

## 3. What the output is used for
The brief is the **upstream artifact in the product pipeline.** Its hypothesis is the input to
`prd-author`: the PRD is generated next, grounded in this evidence. Concretely the output is used to:
- help a product team **decide where to invest** (one brief = one opportunity = one bet);
- **align** stakeholders on a shared, evidence-backed story before any spec exists;
- **seed the PRD** — the hypothesis becomes the PRD's problem + evidence.

```
signals + business_context + company_goal
        |
        v
   evidence-brief        <- THIS SKILL (cluster: 01-discovery-and-research)
        |  hypothesis
        v
   prd-author            <- human PRD (cluster: 04-definition-and-specs)
        |  PRD
        v
   implementation-spec   <- LLM-readable build spec (cluster: 04-definition-and-specs)
```

## 4. When to call it / when NOT to
**Call it when** there is >=1 analysis signal available, a product/strategy decision is pending,
the audience is a product team, and a PRD will follow.
Literal triggers: "evidence brief", "insight brief", "make the case for X", "what's the
opportunity here", "synthesize these signals", "turn this analysis into an insight".

**Do not call it** to write requirements (`prd-author`, after this), to *run* the analysis
(this synthesizes provided analysis; it never fabricates data), or to brief leadership on an
external shock (`leadership-brief`).

## 5. Inputs
| Field | Required | Notes |
|---|---|---|
| `signals[]` | >=1 required | each `{type: data_science / competitive / voc / market, content}` |
| `business_context` | required | what the product is; the behavior under investigation |
| `company_goal` | required | the North-Star / goal the opportunity must ladder up to |
| `baselines`, `segments`, `prior_experiments`, `metric_tree` | optional | strengthen the brief; omit if absent |

**Hard rule:** use only data the user provides. Never estimate, round into existence, or
fabricate quotes. Missing numbers are omitted or named as a gap.

## 6. How it works (run in this order — same inputs -> same brief)
1. Frame the question from `company_goal`.
2. Extract the one finding from each signal (competitive -> an *extraction*: where we're weak -> the opportunity, not a checklist).
3. Converge: surface where >=2 independent signals agree. Don't force it — if they diverge or only one exists, say so and lower confidence. Flag suspected shared causes.
4. Find the wedge (strongest single proof); label its strength (usually correlational).
5. Pick the best chart per finding; sequence the charts so they tell one story.
6. Include real VoC quotes across channels when available and aligned.
7. Write the value-driven hypothesis (behavior change -> business value).
8. Mark the handoff to `prd-author`.
9. Honesty pass: every figure traces to an input; every quote is real; correlation != causation; confidence stated; non-converging signals not hidden.

### Chart selection (variable — best fit, not a fixed set)
| Finding is about... | Use |
|---|---|
| Change over time | line / area |
| Ranking / composition of categories | bar (horizontal for labels) |
| Drop-off across stages | funnel / waterfall |
| Two-group comparison (the wedge) | paired bars |
| Relationship between two measures | scatter |
| Capability gap vs competitors | matrix + an explicit "what we extract" |
| Multiple independent signals agreeing | convergence diagram |

## 7. Reference examples (read these to learn the pattern)
Five worked briefs live in `examples/`. They hold the **structure constant** while varying the
business, chart types, available signals, and convergence strength — so you can see what stays
fixed and what flexes. **Open them in a browser to view; read the source to copy the pattern.**

| File | Business | Signals / convergence | Charts | What it teaches |
|---|---|---|---|---|
| `examples/01-lyra-language-app.html` | Consumer language app | 5 signals, strong | line + paired bars, h-bar, matrix, convergence diagram | the full, high-confidence case |
| `examples/02-northwind-pay.html` | B2B invoicing SaaS | 3 signals, moderate-high | funnel, paired bars, line, 3-node convergence | fewer signals; confidence stated honestly |
| `examples/03-trailhead-fitness.html` | Consumer fitness | 2 signals that conflict | scatter, sentiment stacked bar | NOT forcing convergence; a guarded low-confidence hypothesis |
| `examples/04-cartwheel-marketplace.html` | Resale marketplace | 1 signal (data only), no VoC | conversion bar, distribution histogram | graceful degradation; naming one signal honestly |
| `examples/05-aperture-analytics.html` | B2B analytics SaaS | 4 signals, market-led | area trend, matrix, request bar, convergence diagram | an opportunity that starts outside the company |

**How to use the examples when generating a new brief:**
1. Pick the example whose **signal situation** matches yours (many strong signals -> 01/05; few -> 02; conflicting -> 03; single -> 04).
2. Reuse its **structure and the shared design system** (same CSS, same section order).
3. **Swap in the real findings and choose charts that fit** — don't copy 01's charts if your findings aren't shaped like 01's.
4. **Set confidence by how many independent signals genuinely converge** — and never invent a signal to fill a gap (see 03 and 04 for the honest patterns).

## 8. Guardrails (do not violate)
- No fabricated numbers or quotes; provided data only.
- Convergence must be genuine (independent sources); flag suspected shared causes.
- Correlation != causation; state confidence.
- Exactly one opportunity / one hypothesis per brief.
- If evidence is insufficient, say so — do not manufacture a story.

## 9. Minimal invocation sketch
```
run skill "evidence-brief" with {
  company_goal: "grow subscription retention / LTV",
  business_context: "language app; week-1-active users churn by week 4",
  signals: [
    {type: "data_science", content: <retention + cohort analysis>},
    {type: "voc",          content: <reviews + support + sales notes>},
    {type: "competitive",  content: <top-3 teardown>},
    {type: "market",       content: <AI-voice availability note>}
  ]
}
-> returns: evidence brief (HTML) + hypothesis  -> feed hypothesis to prd-author
```

## 10. Folder contents
```
evidence-brief/
|-- SKILL.md                          # authoritative behavior spec (triggers + method + output + quality bar)
|-- README.md                         # this guide
`-- examples/
    |-- 01-lyra-language-app.html     # 5 signals, strong convergence
    |-- 02-northwind-pay.html         # 3 signals, moderate-high
    |-- 03-trailhead-fitness.html     # 2 signals, conflicting (weak — honest)
    |-- 04-cartwheel-marketplace.html # 1 signal, no VoC (degradation)
    `-- 05-aperture-analytics.html    # 4 signals, market-led
```
`SKILL.md` is authoritative for behavior; this README is the orientation layer; `examples/` are
the calibration set. An agent should read `SKILL.md` to act and skim `examples/` to calibrate.
