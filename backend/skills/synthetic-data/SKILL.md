---
name: synthetic-data
description: Generate realistic FAKE datasets for demos, prototypes, tests, and spec examples — CSV/JSON/SQL — with believable distributions and edge cases, never real customer data. Use when the user says "dummy data", "test data", "sample dataset", "mock data", "seed data", or needs realistic records to demo or test against. Builds plausible, schema-correct, edge-case-inclusive data; explicitly synthetic; never uses or reproduces real PII.
---

# Synthetic Data

## What it does
Produces realistic but entirely **fake** datasets — correct schema, believable distributions, and the edge cases that break software (nulls, max-length, unicode, boundary dates) — for demos, prototype seeding, test fixtures, and worked spec examples. Everything is clearly synthetic.

## When to use / when NOT to use
- **Use** to create sample/test/demo data from a described schema or scenario.
- **Do NOT use** to analyze real data (`saas-metrics-diagnosis`), or as a substitute for real research signal.

## Inputs
- **Required:** the schema/fields or a description of the records needed.
- **Optional:** row count, format (CSV/JSON/SQL), distributions, locale, edge cases to include.

## Method (methodology)
1. **Confirm the schema** — fields, types, relationships, constraints.
2. **Generate plausible values** — realistic names/dates/amounts with sensible distributions (not all identical), referential integrity across related tables.
3. **Seed edge cases on request** — nulls, empties, max-length, special chars, boundary values — so tests hit the corners.
4. **Label as synthetic** and emit in the requested format.

## Output spec
The dataset in the requested format + a one-line note of row count, seed assumptions, and that it's synthetic. For large sets, a generator script rather than thousands of inline rows.

## Security & privacy (enforced)
- **Never uses, reproduces, or "lightly anonymizes" real customer/PII data** — synthetic from scratch only. If handed a real dataset to "expand," it generates new synthetic rows matching the *shape*, not copies of real records.
- Avoids real people's identifying details, real card/SSN-format numbers that could validate, and real secrets/keys.

## Sprntly integration (optional)
- **Inputs:** a schema from the knowledge graph / a spec's data model.
- **Outputs:** fixtures for prototype/demo seeding.
- **Degrades to:** standalone from a described schema.

## Quality checklist (the bar)
- [ ] Schema-correct with realistic, varied distributions (not uniform filler).
- [ ] Referential integrity across related tables.
- [ ] Edge cases included when asked.
- [ ] Clearly synthetic; **no real PII used or reproduced.**

## Known gaps / limitations
- Synthetic data validates plumbing and demos, not real-world distributions or model accuracy.
- Realistic ≠ representative — don't draw product conclusions from it.

## Worked example
**Input:** "200 sample advertiser accounts, CSV, include some disabled + some with failed payments." → CSV with varied signup dates, spend, status (mostly active, some disabled, some payment-failed), nulls in optional fields. Synthetic, labeled.
