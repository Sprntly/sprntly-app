---
name: interview-synthesis
description: Turn a set of qualitative interviews — 1:1 calls, in-person sessions, roundtables/focus groups, contextual inquiry/field visits, diary studies, usability sessions, win/loss or churn-exit interviews — into one clear, report-style synthesis the team can act on. It opens with the research context (how many, what method, which segments, when, who ran it), then a voice-first TL;DR that enumerates what was learned with real customer quotes and a takeaway each; goes deep on themes (each with how many participants raised it, the job/pain behind it, 2–3 real quotes, sentiment, whether it's persistent or new, and whether it ALSO shows up in other signal sources); shows a top-pain-points view with severity and — only if real behavioral/commercial data exists — correlation to core metrics; surfaces surprises, disconfirming evidence, and what's still unknown; and ends in action-card recommendations (Create brief / Add to backlog). Built for qualitative rigor: real quotes only (never fabricated), small-n reported as counts not survey percentages, correlation never claimed as causation, method bias flagged. Use when the user says "synthesize these interviews", "what are the themes", "analyze user research", "what did we learn from these calls", or pastes interview notes/transcripts.
---

# Interview Synthesis (qualitative research → themes → recommendations)

## What it does
Turns raw qualitative interviews into a synthesis a team decides from — and makes the **voice of the customer the lead, not a footnote**. The report *opens with customers speaking* and only then explains what it means. It reads top to bottom:
1. **Voices — in their words (FIRST)** — the most powerful real quotes, large and attributed, so the customer is the first thing you hear.
2. **TL;DR** — one line on what the voices add up to, then the key learnings `#1/#2/#3`, each carrying *what they're saying (a real quote) · what the problem is · what it means to us · what we can do.*
3. **Themes (quote-led)** — each opens with its real quotes, then a compact read: **signal strength** (how many of N), the **job/pain**, persistent-vs-new, **cross-source convergence**, severity, and metric correlation *if real data exists*.
4. **Top pain points** *(when volume is large enough)* — a glance table with participant count, severity, and metric correlation when the data exists.
5. **Surprises · disconfirming · what we still don't know** — the rigor layer.
6. **About this research** — methodology and context (n, method, segments, when, who, recruiting, saturation, biases), placed *after* the voice rather than gating it.
7. **Recommendations** — top actions as cards, each with a **Create brief** (→ `evidence-brief` / `prd-author`) or **Add to backlog** (→ `prioritize`) CTA.

## What counts as an "interview" (and why method matters)
An interview is qualitative signal gathered through direct conversation or observation, including: **1:1 interviews** (video/phone/in-person), **roundtables / focus groups**, **contextual inquiry / field studies**, **diary studies**, **usability/observed sessions**, **win/loss** and **churn-exit** interviews. The skill records and accounts for the method because it changes interpretation — a **roundtable** carries groupthink risk, a **CSM- or sales-led** call carries rapport/leading bias, **usability** is observed behavior (stronger than stated preference), **exit interviews** over-weight the unhappy. It flags these rather than treating all sources as equal.

## When to use / when NOT to use
- **Use** after qualitative research to find patterns, strength, and the decision they support.
- **Do NOT use** to design the interviews (`interview-guide`), to build the opportunity map (`opportunity-tree` / `continuous-discovery`), to mine public reviews (`public-feedback-report`), or to synthesize curated multi-channel feedback at scale (`voice-of-customer-report`). Interviews specifically → here.

## Inputs
- **Required:** interview notes or transcripts (one or many).
- **Strongly helpful (used if present):** the learning goal; per-participant **segment/role**; the **method & date** of each session; **n** of interviews; who conducted them; recruiting criteria.
- **Optional (unlocks deeper output):** behavioral/usage or commercial data (churn, $, activation) to correlate severity to metrics; prior studies or other-source signals (`feedback-synthesis`, `public-feedback-report`, support) for convergence and persistent-vs-new.
- *If anything is missing, infer and state the apparent goal, flag low-n, and degrade gracefully — never invent it.*

## Method
0. **Scope & context.** Capture the learning goal; the **corpus** (n interviews, method(s), segments, dates, who ran them, recruiting); and **data on hand** (metrics for correlation, other-source signals for convergence).
1. **Normalize.** Each excerpt: verbatim · participant ID · segment/role · method · date. (If notes aren't verbatim, mark close paraphrases as paraphrase — never dress them as quotes.)
2. **Code & cluster** observations into themes; **track which participants** raised each (for signal strength). Separate "many said" from "one said vividly."
3. **Signal strength & saturation.** Per theme: **count of N participants** (e.g. "7 of 9"), consistency, and a confidence label (strong / moderate / emerging). **Report counts, not survey-style percentages** — qualitative ≠ statistical significance.
4. **Jobs / pains / gains** per theme; note any **say-do gap** (stated vs observed).
5. **Voice — real quotes only.** 2–3 verbatim, attributed quotes per theme (participant + segment). Never invent, never overstate; if a theme lacks a strong quote, say so.
6. **Persistent vs new.** Is the theme recurring across prior studies / over time, or first heard this round?
7. **Cross-source convergence.** Check each theme against other signals you have (`feedback-synthesis`, `public-feedback-report`, `voice-of-customer-report`, support). Note where it's **independently confirmed** — convergence of independent sources strengthens confidence — but **never force it**; absence is reported plainly.
8. **Severity & metric correlation — only with real data.** Rate severity from the language; **correlate to a core metric (churn, activation, revenue) ONLY if you actually hold that data**; otherwise label "qualitative only — not correlated." Correlation ≠ causation; say so.
9. **Surprises, disconfirming evidence, open questions, saturation.** Actively surface what contradicts the pattern and what's still unknown (anti-confirmation-bias); note where saturation was/ wasn't reached.
10. **Recommend.** The top ~5, each tied to the learning + signal strength + metric, as **action cards** with a **Create brief** or **Add to backlog** CTA.

## Output spec (voice-led — the customer speaks first; locked order)
**A. Title (minimal).** A short eyebrow + title and a one-line context chip (e.g. "17 conversations · last quarter") — nothing more up top. No heavy run-line of metadata here; it would bury the voice.
**B. Voices — in their words (FIRST, the hero).** 3–4 of the **most powerful verbatim quotes**, displayed large and attributed (role + context like "churned" / "ready to expand"). The customer speaks before any analysis — this is the emotional and evidentiary lead, not a sidebar.
**C. TL;DR.** One-line synthesis of what the voices add up to, then the key learnings enumerated `#1/#2/#3`, each still **voice-led**: a real quote + the problem + what it means to us + what we can do.
**D. Themes (quote-led).** Each theme **opens with its 2–3 real quotes, large**, then a compact read beneath: **signal strength (n of N)**, the job/pain, persistent-vs-new, cross-source convergence, severity, and metric correlation *if real data exists*. Quotes are the visual anchor; the analysis supports them, not the reverse.
**E. Top pain points** *(only when volume is large enough)* — compact table: pain · participants (n/N) · severity · metric impact (if data) · persistent/new · also-seen.
**F. Surprises · disconfirming · what we still don't know** — incl. saturation/confidence and method-bias caveats.
**G. About this research (methodology/context) — placed DOWN here, not at the top.** n, method(s), segments, dates, who ran it, recruiting, saturation, and the bias flags. It's essential context, but it follows the voice rather than gating it.
**H. Recommendations** — top ~5 action cards, each with the supporting learning + signal strength + metric, and a **Create brief** (→ `evidence-brief` / `prd-author`) or **Add to backlog** (→ `prioritize`) CTA.
**I. Integrity & confidence note** — n, methods, biases, quotes-real, no-fabricated-metrics, convergence provenance, what would strengthen.

Design intent: **the report should feel like the customers talking, not like a corporate document.** Quotes large and first; metadata demoted. Render as a clean artifact; fall back to structured Markdown if no rich surface. A worked example ships at `examples/` — reference it for the exact look.

## Adaptive depth
- **Small-n (a handful of interviews):** qualitative themes + signal strength as counts; no top-pains table, no metric correlation; emphasize hypotheses-to-validate and open questions.
- **Larger volume (many interviews / multiple methods):** add the **top-pain-points table**, severity distribution, and — if behavioral/commercial data is linked — metric correlation. The skill declares which mode it's in.

## What makes this complete (the pieces often missed)
- **Methodology & context up front** — n, method, segments, when, who, recruiting — without it, findings can't be weighed.
- **Signal strength as counts + saturation** — "7 of 9," not "78%"; and whether themes saturated.
- **Real voice throughout** — attributed verbatim quotes, the heart of the report.
- **Persistent vs new** — is this chronic or just-surfaced.
- **Cross-source convergence** — interview theme also seen in reviews/support/feedback dramatically raises confidence; shown explicitly, never forced.
- **Say-do gap, segment differences, surprises, disconfirming evidence, open questions** — qualitative rigor that stops cherry-picking.
- **Method-bias flags** — roundtable groupthink, CSM/sales-led rapport bias, exit-interview skew, leading questions.
- **Metric correlation only with real data** — severity tied to churn/activation/$ when held; labeled qualitative-only otherwise.
- **Recommendations with a CTA** — every top finding can become a brief or go to the backlog.

## Guardrails (against fake data & overreach)
- **Real quotes only**, attributed; never fabricate, never turn a paraphrase into quotation marks, never overstate the voice.
- **Small-n honesty** — counts not inferential percentages; qualitative findings are directional hypotheses to validate, not proof.
- **No fabricated metrics** — correlate to a core metric only with real data; **correlation ≠ causation**, stated.
- **Convergence never forced** — only independent, real sources; absence reported plainly.
- **Method & recruiting bias flagged** — and survivorship in exit interviews (the most damaged users may already be gone).
- **Disconfirming evidence sought**, not buried.

## Sprntly integration (optional)
- **Inputs from Sprntly:** transcripts and diarized calls from connected sources; the OST opportunity the research targets.
- **Outputs to Sprntly:** themes written back as opportunities/evidence on the OST; confidence updates to related findings in the outcome graph.

## Quality checklist (the bar)
- [ ] **Reads top-to-bottom: voice-first TL;DR → methodology → themes → (top pains) → surprises/unknowns → recommendations.**
- [ ] TL;DR is **enumerated `#1/#2/#3`**, each with a **real quote + problem + meaning + action**, voice leading.
- [ ] **Methodology/context present** (n · method · segments · dates · who · saturation), biases flagged.
- [ ] Themes carry **signal strength (n of N)**, **2–3 real quotes**, persistent/new, and **cross-source convergence**.
- [ ] **Top-pains table only when volume warrants**; **metric correlation only with real data**, labeled.
- [ ] **Surprises / disconfirming / open questions / saturation** surfaced.
- [ ] **Recommendations capped ~5**, each an action card with **Create brief / Add to backlog**.
- [ ] **No fabricated quotes or metrics; small-n reported as counts; correlation ≠ causation.**

## Known gaps / limitations
- Qualitative work shows *what* and *why*, not *how many* in the population — it generates hypotheses; surveys/analytics size them.
- Small, non-random samples don't generalize; recruiting and method bias shape what's heard.
- Exit interviews suffer survivorship — the angriest churned users may never have agreed to talk.
- Cross-source convergence raises confidence but isn't proof; the resolving experiment settles causation.
