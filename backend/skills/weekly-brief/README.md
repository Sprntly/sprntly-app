# Weekly Brief — usage guide

This file orients any LLM or agent that needs to use this skill. It explains what the skill produces, when to reach for it, the exact loop to follow, and the input/output contracts. `SKILL.md` is the authoritative spec; this README is the front door and quickstart.

## What it does

Converts one or more **completed analyses** into a decision-ready PM brief: a 3-line opening that frames the upside, followed by 3–7 ranked recommendation cards. Each card names a problem with a number, states what acting is worth, and hands over a solution that is already drafted — so the recipient reviews and approves rather than starts from scratch. Works for any finding type (reliability/bug, retention/churn, competitive, growth, demand, engagement, compliance).

It does **not** run analysis or compute metrics. It phrases findings it is given. Every number in the output must come from the input.

## When to invoke

Use this skill whenever the task is to turn analysis output, signals, or findings into a brief, weekly digest, home-screen recommendations, or any "what should the PM act on this week" summary — including indirect phrasings like "summarize these findings," "make the brief," "surface recommendations," or "what's worth my attention." If you have findings and a reader who must decide what to do, this is the skill.

## How to use it — the loop

1. **Read `SKILL.md` in full.** It is the contract: gate → prioritize → classify → greeting → write cards → self-critique → render.
2. **Read `references/signal-schema.json`.** This is the input you must receive or assemble. If the upstream data isn't in this shape, map it into this shape first. Confirm each numeric you'll surface has a backing field — if it doesn't exist, you will not invent it.
3. **Gate and select** the signals (confidence floor, evidence requirement, dedupe, dismissal memory, staleness) per SKILL.md step 1.
4. **Compose the `brief` object** following SKILL.md steps 2–5: prioritize and order, classify (type + accent), write the 3-line greeting, then each card (pain-plus-value title, self-contained ≤4-line body with the why → worth → review-and-approve arc, source chips, paired CTAs).
5. **Self-check** against `references/rubric.md` — run the blocking linters first, then the scored rubric; rewrite any card that fails a hard gate once. Then compare voice and shape to `references/examples.md` (golden brief + counter-examples).
6. **Render** by populating `assets/brief-template.html`, or return the `brief` JSON as the source of truth and let a downstream renderer fill the template.

## Input contract (quick reference)

A `brief_request` = a list of `signal` objects + light context (recipient name, company scale). Each `signal` carries: `type` (closed enum), `pain` (the problem stat), `value` (the upside of acting, with `verb`, `amount` *or* `range`, and a required `basis`), `story`, `recommended_action`, `prd_ref`/`prototype_ref`, `sources`, `evidence`, `confidence`, `urgency`, `reach`, and dismissal/recency flags. Full structure and field rules: `references/signal-schema.json`.

Key rules: numbers are inputs (never computed here); prefer ranges over false precision; `value.amount: null` → the title uses a qualitative value and no dollar figure appears; missing `prd_ref` → the CTA becomes "Draft PRD" instead of "View PRD".

## Output contract (quick reference)

A `brief` object: `recipient`, `greeting` (≤3 lines, offensive framing, totals = sum of card figures), `cards[]` (each with `type`, `accent`, `title`, `body`, `sources`, two `ctas`, and a `signal_id` backlink), and `suppressed[]` (signals deliberately not shown, with reason — supports dismissal memory and the audit trail). The HTML render is a view of this object.

## Rendering notes

All visual tokens — the bold serif titles, the green CTAs, the type-accent colors, the source chips, the regenerate/dismiss controls, the gray-out-with-undo behavior — live in `assets/brief-template.html`. Fill the slots; do not restyle. CTA labels come from a fixed set (`View PRD` / `Draft PRD`, `View prototype` / `Generate prototype`), primary then ghost, in the same place on every card.

## Quality gates (must pass before emitting)

Treat these as blocking (full list in `references/rubric.md`): every figure traces to a source; greeting total equals the sum of card figures; each title has both pain and value; body ≤4 lines and reads with the title removed; type is in the taxonomy and the accent matches it and the valence (never a gain color on a loss); exactly two CTAs with correct labels; no priority labels; no "N signals agree" or confidence-bar widgets; source chips match the real sources.

## Edge cases (handle explicitly)

No value figure → qualitative value, no fabricated number. Single dominant signal → one honest chip, no faked convergence. Sparse/cold start → fewer cards, state lower confidence plainly. Conflicting signals → narrate the tension. Quiet week → an honest short brief, never manufactured urgency. Stale/resolved → suppress. Dismissed-before-and-unchanged → suppress; materially worse → resurface with the reason. Non-monetary impact → the value clause flexes to %, points, time, NPS. Tiny impact vs. company scale → down-rank or drop.

## Minimal end-to-end shape

Input (abbreviated — one signal):
```json
{ "recipient": "David", "company_scale_arr": "120M",
  "signals": [{
    "id": "sig_checkout_ios", "type": "reliability",
    "pain": { "metric": "iOS checkout failure rate", "value": "1 in 6", "context": "final step, silent crash" },
    "value": { "verb": "recover", "amount": "$2.2M", "basis": "failed-checkout volume × AOV, annualized", "confidence": 0.9 },
    "story": "A silent failure at the final checkout step has been crashing the iOS app for three weeks.",
    "recommended_action": "Retry on the failing call plus a clear error state.",
    "prd_ref": "prd_1042", "prototype_ref": "proto_1042",
    "sources": ["Sentry", "Analytics", "Billing"],
    "evidence": ["4,100 exceptions in 3 weeks", "drop-off isolated to final step"],
    "confidence": 0.94, "urgency": "high"
  }]
}
```
Output (abbreviated — the card this yields):
- **Title:** A login bug is failing 1 in 6 iOS checkouts — the fix recovers about $2.2M a year.
- **Body:** For three weeks, a silent failure at the final checkout step has been crashing the iOS app with no error message … review and approve it to put the recovery in motion.
- **Sources:** Sentry · Analytics · Billing — **CTAs:** View PRD · View prototype

See `references/examples.md` for the full golden brief (greeting + five cards) and the counter-examples to avoid.

## Files in this skill

- `SKILL.md` — authoritative spec and workflow. Read first.
- `references/signal-schema.json` — input `signal` and output `brief` structures.
- `references/rubric.md` — blocking linters + scored rubric for the self-check pass.
- `references/examples.md` — golden reference brief and annotated counter-examples for alignment.
- `assets/brief-template.html` — canonical render template; all visual tokens live here.
