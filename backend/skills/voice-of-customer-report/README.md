# voice-of-customer-report

Turn your company's own feedback corpus — CSM calls, support tickets, customer
and churn interviews, sales notes — into one decision-grade Voice of Customer
report: user problems with real counts, paired volume/severity ratings, the
metric each problem impacts and by how much (revenue always shown), and only
the 5–7 most important recommendations.

**Replaces:** `third-party-feedback` and `voc-volume-severity` (merged).
**Sibling:** `public-feedback-report` handles public channels (app stores,
Reddit, X, review sites); this skill never touches them.

## What you get

| Section | What it does |
|---|---|
| TL;DR panel | Sources as chips, one-line arc, top findings as user problems with VOL/SEV pills and an impact line each |
| User problems at a glance | `User problem \| Volume \| Severity \| Metric it impacts \| By how much` — volume & severity adjacent on the same low/med/high scale; revenue on every row (figure or 🅘 unknown, never estimated) |
| Themes | Quote-led cards, 2 per row — verbatim customer words, counts not percentages, persistent/new tags |
| Recommendations | 5–7 max, enforced by a rendered prioritization gate; each card explains what the action is in plain sentences before why it ranks; Generate PRD / Move to backlog CTAs |

## Signature moves

- **🔇 Silent-killer flag** — low voice volume + high severity or dollars gets
  elevated above its mention count (e.g. 5 of 17 accounts, but $170K expansion
  gated).
- **Prioritization gate** — candidates are ranked by impact; top 5 selected,
  extended to 7 only on a tie at the cut; the gate line is printed in the
  report as proof it ran. No 20-item lists, ever.
- **Confidence tiers** — 🅗 counted from the corpus · 🅢 stated but unverified ·
  🅘 inferred/unknown. Unknown revenue is labeled, never estimated, and the gap
  itself becomes an instrumentation recommendation.

## Files

- `SKILL.md` — the skill definition (method, output contract, hard rules,
  quality bar)
- `example-output.html` — a full sample report (illustrative data,
  banner-labeled)

## Version

v2 — 2026-07-03. Adds: user-problem framing rule, redesigned TL;DR panel
(light tint, source chips), split Metric / By-how-much columns with the
always-present revenue line, adjacent volume/severity pill scales, the
prioritization gate, plain-sentence recommendation descriptions, and removal
of the basis footer.
