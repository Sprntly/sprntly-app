---
name: business-context
description: Build a structured, provenance-tracked business context for a company from just its name, website, and goals — the organizational "lens" downstream agents read every signal through. Use when the user says "build business context", "set up our company profile", "onboard this business", "what do we know about this company", "create the org context", or when a new account joins and we need to understand their model, users, and goals before interpreting anything. Produces both a structured object (for a knowledge graph) and a readable brief, with every field tagged given / inferred / unknown + confidence — and it never fabricates.
---

# Business Context

## What it does
Turns the thin inputs a company gives at onboarding — **name, website, goals** — into a rich, structured **business context**: what they do, how they make money, who their users are, what their product's value moments are, their goals, and (critically) their *own vocabulary* for things like "user," "activation," and "churn." This context is the **organizational lens** every downstream agent reads through: when Sprntly later interprets a signal, it can say "for a usage-priced marketplace like this, that drop in second-week repeat-listings matters" instead of a generic read. It also lets Sprntly *speak the customer's language* back to them.

It runs **autonomously** — it fetches and reads the website, derives the context, and labels every field by provenance (**given** by the user / **inferred** from the site / **unknown**) with a confidence level. It does **not** stop to ask the user to confirm; instead it is rigorously honest about what's known vs. derived vs. missing. It emits **two artifacts**: a structured object (JSON/YAML, for the knowledge graph) and a readable brief (for a human).

## When to use / when NOT to use
- **Use** at onboarding, or whenever the org context is missing/stale, to establish the lens before interpreting signals, writing to the customer, or making recommendations.
- **Do NOT use** for a deep competitor study (`competitive-intelligence-review` — this captures only a lightweight market layer and defers depth to it), for positioning (`positioning`), or for the customer's *own* customers' personas (`persona-segment`). This is the company-level context, not a teardown and not a persona.

## Inputs
- **Required:** company name, website URL, and their stated goal(s).
- **Optional:** anything else they share (deck, ARR, segment notes, metrics, existing definitions). *More given data = fewer inferences; the skill uses whatever it's handed and reads the site for the rest.*

## Method (methodology)
A descriptive-ontology build (the "business dictionary" an agent consults) + GTM enrichment layers (firmographics / model / users / product), with provenance-first discipline so a wrong inference can't silently poison downstream agents.
1. **Ingest the givens.** Record name, website, goals exactly as given (these are the highest-confidence facts), tagged `src: given`.
2. **Read the site (and any provided material).** Fetch the homepage, product, pricing, about, customers, and careers pages where available; extract what they do, model, segments, product, vocabulary. Tag what's derived `src: inferred`, with the page it came from.
3. **Fill the layers** (below), capturing the business's **own words and definitions** — their term for users, their definition of activation/churn — not generic ones. This vocabulary layer is what makes Sprntly's later communication sound native to the business.
4. **Tag every field: provenance + confidence + date + evidence.** `src` ∈ {given, inferred, unknown}; `conf` ∈ {high, med, low}; `as_of` date; and for every `inferred` field, **the exact source snippet that backs it**. A field with no snippet to point to isn't `inferred` — it's a guess, and becomes `unknown`. An inference from a clear pricing page is high-conf with its snippet; a guess about unit economics from nothing is `unknown`.
5. **Never fabricate.** If a field can't be sourced or reasonably inferred, set it `unknown` (value null) — never invent a revenue figure, segment, or definition. A hallucinated business model corrupts every agent that reads this context, so honesty here is load-bearing.
6. **Derive the "so what" for downstream use.** End with what *matters to this business* — what a good outcome looks like for them — so later signal interpretation can judge "does this move their needle?"
7. **Verify before delivering (catch anything made up).** Run a groundedness pass — the `fact-check` skill — over the built context: every `inferred` field must bind to a real source snippet, every number must appear verbatim in a source or be a shown derivation, and anything unsupported is downgraded to `unknown` (and omitted from the brief). This is the active check, not just the no-fabricate rule. No context ships with an unverified claim presented as fact.
8. **Emit both artifacts** and set a **refresh trigger** (when this should be rebuilt — e.g. pricing change, new segment, stale after N months). The two artifacts differ on unknowns: the structured object records them explicitly (`null` + `unknown`) so the system knows what to pull; the readable brief **omits** them so a human reads only what's actually known.

## The layers (the schema)
1. **Identity & firmographics** — one-line what-they-do, industry/sub-vertical, size, stage, geography, markets served.
2. **Business model & economics** — model type (subscription / marketplace / transactional / ads / usage / services), revenue & pricing model, **who pays vs. who uses**, monetization unit, rough economics shape, and *what a good outcome looks like for them*.
3. **Users & segments** — the segments, the **job-to-be-done per segment**, and who is buyer vs. user vs. champion (so a signal "from a user" is read as the right *kind* of user).
4. **Product & value** — what the product does, core value moments, and the **activation definition in their terms**, key features, platforms.
5. **Market & competition (lightweight)** — category, main alternatives, positioning angle. Deep work is deferred to `competitive-intelligence-review`.
6. **Goals & strategy** — their stated goal, North Star (if any), current priorities, known constraints. The lens for judging signals.
7. **Vocabulary & definitions** — the business's own words and their meanings (their term for "users," their definition of "active," "churn," "power user"), plus where it differs from Sprntly's default reading. The piece most schemas miss; the piece that makes Sprntly communicate well.
8. **Meta** — created / last-refreshed dates, refresh trigger, source list, and an overall-confidence read.

## Output spec
Two artifacts (see `templates/business-context-schema.yaml` and `templates/business-context-brief.md`):
1. **Structured object** — YAML/JSON covering all eight layers; every leaf carries `value`, `src` (given/inferred/unknown), `conf` (high/med/low), `as_of`. Unknowns are explicit (`value: null, src: unknown`), never omitted-as-if-known and never guessed. This is the knowledge-graph-ingestible form.
2. **Readable brief** — a one-page summary **written for the company's own team to read**, in a clear, natural tone, organized under plain headers (e.g. *About the business*, *Business model*, *User groups*, *Product & value*, *How they describe their world*, *Market*). It includes **only what we actually know** — fields we couldn't source are **omitted entirely, not listed as "unknown"** (the structured object above already records the gaps for the system, so the human brief stays clean). Omitting is not fabricating: we never invent a fact, we just don't show an empty one.

## Sprntly integration (optional)
- **Inputs from Sprntly:** any connected data (product analytics, CRM, billing, support) to raise confidence and replace inferences with facts; prior context versions from the knowledge graph.
- **Outputs to Sprntly:** the structured object becomes the root organizational-context node in the knowledge graph that every agent reads; the vocabulary layer configures how Sprntly phrases things back to the customer; the "what good looks like" feeds signal-ranking; the refresh trigger becomes a monitored condition.
- **Degrades to:** fully standalone — give it name + website + goals and it produces both artifacts from a web read alone, labeling confidence honestly.

## Quality checklist (the bar)
- [ ] **Runs autonomously** — no "please confirm" step; instead every field is labeled given / inferred / unknown with confidence.
- [ ] **No fabrication — and it was actively checked.** A `fact-check` groundedness pass was run: every inferred field binds to an exact source snippet, every number is verbatim-or-derived, and unsupported claims were downgraded to `unknown`. Nothing (especially revenue model, segments, definitions) is invented.
- [ ] **Their vocabulary is captured** — the business's own terms and definitions, not generic ones, with deltas from Sprntly's defaults flagged.
- [ ] **Who-pays vs. who-uses** is distinguished, and each segment has a job-to-be-done.
- [ ] Ends in **what a good outcome looks like for them** (the lens for downstream signal interpretation).
- [ ] **Both artifacts** emitted, and a **refresh trigger** is set. The **readable brief** is written for the company's own team, uses clean section headers, and **shows only known facts** — unknown fields are omitted from the brief (the structured object still records them so Sprntly knows what to pull).

## Known gaps / limitations
- A website tells you what a company *says*, not always what's true — site-derived fields are inferences, labeled as such and bound to a snippet by the `fact-check` pass; connected first-party data (Sprntly) is what raises them to fact.
- Private companies expose little on economics; those fields will often be `unknown`, and that's the correct output, not a guess.
- Context decays — a stale context silently corrupts downstream agents, so the refresh trigger isn't optional.
- It captures the company; it doesn't analyze the competition (`competitive-intelligence-review`) or the company's end-user personas in depth (`persona-segment`).

## Worked example
**Input:** name "Frazil"; website (a frozen-beverage/Frostline brand site); goal "grow repeat orders from convenience-store operators."
**Output (abridged):**
- **Structured object** (excerpt): `identity.one_liner: {value: "frozen beverage program for c-stores & foodservice operators", src: inferred, conf: high, as_of: 2026-05-30}`; `business_model.who_pays: {value: "store operators / distributors", src: inferred, conf: med}`; `business_model.who_uses: {value: "end consumers at the dispenser", src: inferred, conf: high}`; `economics.unit_economics_shape: {value: null, src: unknown}` (not on the site → not guessed).
- **Vocabulary:** their "operator" = the paying customer (a store), distinct from the "consumer" who drinks it — so a signal about "users" must be read as *operators* for revenue and *consumers* for demand. Captured so Sprntly doesn't conflate them.
- **Goals:** stated goal = repeat orders from operators → "good outcome" = operator reorder rate, not consumer volume; downstream signal-ranking should weight operator-retention signals highest.
- **Readable brief:** one page leading with what they do, who pays vs. drinks, the operator/consumer vocabulary distinction, the reorder goal, and a visible list of the `unknown`/low-confidence fields (economics, exact segment sizes) flagged for a data pull.
