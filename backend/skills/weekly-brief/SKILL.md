---
name: weekly-brief
description: Generate the weekly PM brief — a short opening synthesis plus ranked recommendation cards, each leading to View PRD / View prototype CTAs — from one or more completed analyses. Works for any finding type — a bug, a revenue opportunity, a competitive threat, a churn risk, a demand signal, a compliance change, or an engagement gap. Use this skill whenever you need to convert analysis output, signals, or findings into a brief, weekly digest, home-screen recommendations, or any "what should the PM act on this week" summary — even when the user only says "summarize these findings," "make the brief," "surface recommendations," or "what's worth my attention."
---

# Weekly Brief

Produce a brief that a busy PM can read cold and act on: a 3-line opening that frames the upside, then a ranked set of cards. Each card names the problem with a number, states what acting is worth, and hands over a solution that's already drafted — so the PM reviews and approves rather than starts from scratch.

## The one principle that makes this reliable

**Numbers are inputs, never outputs.** Every figure in the brief — the pain stat, the value of acting, the totals in the greeting — must come from the analysis you were handed. This skill *phrases* findings; it never computes or invents them. If the analysis didn't produce an impact figure, do not manufacture one (see Edge cases). This single rule prevents the most damaging failure: a confident, persuasive headline built on a made-up number.

Pin everything that shouldn't vary (layout, colors, taxonomy, CTA placement — all in `assets/brief-template.html`) and generate only the prose. Consistency comes from writing less freely, not more.

## Input

You receive a `brief_request`: a list of `signal` objects from the analysis layer plus light context (the recipient's name, company scale). Each signal carries its type, the pain stat, the value-of-acting (with its basis and confidence), a one-line cause, the recommended action, references to the PRD and prototype if they exist, the sources that fed it, evidence, confidence, urgency, and reach. Full structure in `references/signal-schema.json` — read it before composing.

## Workflow

### 1. Gate — decide what deserves to appear

Restraint is part of quality; a brief full of noise loses trust faster than a sparse one.

- **Surfacing threshold:** drop any signal below the confidence floor (default 0.6) or without at least one concrete piece of evidence. A weak signal is silence, not a card.
- **Dedupe:** if several signals trace to the same underlying issue, merge them into one card. Three angles on one bug is one card, not three.
- **Dismissal memory:** if a signal was dismissed in a recent brief and nothing material changed, suppress it. If it got materially worse, resurface it and say so ("flagged again — now affecting 2× the accounts").
- **Staleness:** drop anything already resolved or shipped.

### 2. Prioritize — order by leverage, not recency

```
priority = 0.40·impact_norm + 0.25·confidence + 0.20·urgency + 0.15·reach_norm
```

`impact_norm` is the value-of-acting normalized to company scale from context — $2.2M is a five-alarm fire at one company and a rounding error at another, so the same number should not always rank the same. If no company scale is available, rank within the brief. Cap the brief at 3–7 cards (default 5); break ties by urgency, then confidence. Order strongest first.

### 3. Classify — assign type and color

Use this closed taxonomy. The tag shows the category only (no P0/P1 priority labels). Color must match valence — **never show a gain color on a loss.**

| Type | When | Accent |
|---|---|---|
| Reliability | bugs, breakage, latency, broken data/tracking | `#c0473c` clay |
| Retention | churn, downgrades, satisfaction drops, complaint spikes | `#b23b52` rose |
| Competitive | a rival move threatening share or renewals | `#b07a2e` ochre |
| Growth | expansion, upsell, new revenue, pricing | `#1a8a52` green |
| Demand | feature asks, sales-driven requests, latent needs | `#5f57a6` iris |
| Engagement | activation, adoption, retention behavior | `#3f63a0` slate blue |
| Compliance | regulatory change, audit/data-residency risk | `#4f5675` deep slate |

Adding a type is a deliberate edit here — not a model choice at runtime.

### 4. Write the greeting — 3 lines, offensive framing

Address the recipient by name. Lead with the work done and the upside on the table, roll up the totals, and name the top one to three plays. Frame it as money to go capture, not fires to put out.

> Good day, [Name] — I've scouted everything across your tools, and there's real upside on the table this week: roughly **$60M within reach**. The strongest plays are closing the gap a competitor just opened, capturing **$8.4M** from accounts primed to expand, and clearing the friction costing your highest-spend users. Five ranked below; the top three move the most.

Maximum three lines. Totals must be the sum of figures actually present in the cards. On a quiet week, say so honestly — never manufacture urgency ("Quiet week — one thing worth your attention.").

### 5. Write each card

**Title — pain, then value.** State the problem with a concrete number, then what acting is worth. This is the single most important pattern in the skill.

- Reliability: *A login bug is failing 1 in 6 iOS checkouts — the fix recovers about $2.2M a year.*
- Engagement: *50% of new users never reach the action that drives retention — guiding them there could lift it ~18%.*
- Growth: *42 accounts have outgrown their plan — claiming them adds $8.4M in expansion.*

Lead with the pain stat; the value clause uses an action verb (recover, protect, add, lift, unlock) plus the figure or range. Keep it tight (~16 words). **If the analysis gave you no value figure, use a qualitative value** ("…closing the biggest gap in the funnel") — never invent a number to fill the slot. The value half is a projection, so it must arrive from the analysis with a basis attached; this is exactly where a fabricated number does the most damage, because the headline is the most persuasive line.

**Body — self-contained, max 4 lines, three beats.** It must read completely on its own with the title removed:

1. *Why it's happening* — name the subject explicitly (not "it has been live three weeks," but "a checkout failure has been live three weeks").
2. *What it's worth* — the impact, bold the headline number.
3. *Lead to the action* — present the solution as already done: "We've drafted the fix as a PRD … review and approve it to [outcome]." The PM's job is approve-and-move-on, not build-from-scratch.

Do not enumerate the source tools in the prose — the source chips carry provenance. Keep the story on the story.

**Sources, CTAs, controls.** A quiet "From" row of source chips (honest: if one source carried it, show one — never imply convergence that didn't happen). Then the paired CTAs in the same place on every card: **View PRD** (green primary) and **View prototype** (ghost). If the PRD or prototype doesn't exist yet, change the label to **Draft PRD** / **Generate prototype** rather than linking to nothing. Each card carries a regenerate icon and a dismiss (×) top-right; dismissing grays the card with an Undo.

### 6. Self-critique, then revise once

Before emitting, score the draft against `references/rubric.md`. If any hard gate fails — a number without a source, a body that needs the title to make sense, a color that mismatches valence, a missing/extra CTA, a title without both pain and value — rewrite that card once and re-check. This mirrors a critique pass and catches most defects before they ship.

### 7. Render

Populate `assets/brief-template.html`. Layout, tokens, fonts (Spectral 600 bold serif titles, Inter body), the green CTAs, chips, and the dismiss/undo behavior all live in the template — the renderer only fills slots. Emit the structured brief object (`references/signal-schema.json` → `brief` schema) as the source of truth; the HTML is a view of it.

## Edge cases

- **No value figure:** qualitative value clause, never a fabricated number.
- **Single dominant signal:** one card, one honest source chip; don't fake convergence (a 1,000% complaint spike is allowed to stand alone).
- **Sparse data / cold start:** fewer cards, lower confidence stated plainly, no confident guesses.
- **Conflicting signals:** narrate the tension ("sales says X; usage says otherwise") rather than silently picking one.
- **Quiet week:** an honest short brief beats five manufactured P0s.
- **Stale / resolved:** suppress.
- **Dismissed before:** suppress unless materially worse, then resurface with the reason.
- **Non-monetary impact:** the value clause flexes to %, points, time saved, NPS — the pain-then-value shape stays the same.
- **Impact tiny relative to scale:** down-rank or drop; don't dramatize a rounding error.
- **Too many high-severity at once:** calibrate; crying wolf every week destroys the signal.

## Guardrails

- Figures are inputs; prefer ranges over false precision; every figure traces to a source.
- Honor the surfacing gate — quality includes staying quiet.
- Honest provenance; never imply more convergence than happened.
- Recommendations stay inside what the PRD actually scopes — no inventing roadmap.
- Respect see-vs-save: derived intelligence in the card, raw PII / customer names only where the workspace permits.
- Keep an audit trail of why each card surfaced (which signals, what logic) — it's defensible and supports a human-at-every-gate posture.

## Reference files

- `README.md` — orientation and quickstart for any LLM/agent using this skill.
- `references/signal-schema.json` — input `signal` and output `brief` JSON structures. Read before composing.
- `references/rubric.md` — scoring rubric and the deterministic linter checklist used in step 6.
- `references/examples.md` — golden examples (signal → card) and counter-examples with why-they-fail.
- `assets/brief-template.html` — canonical render template; all visual tokens live here.
