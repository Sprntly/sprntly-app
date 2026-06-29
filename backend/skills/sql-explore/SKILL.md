---
name: sql-explore
description: Translate a plain-language analytics question into a correct, READ-ONLY SQL query a PM can run to answer it — across common dialects — with the assumptions and caveats stated. Use when the user says "write a SQL query", "query for X", "how do I pull Y", "turn this question into SQL", or wants self-serve analytics. Generates SELECT-only queries (never destructive), states schema assumptions, explains the query in one line, and flags where a result could mislead. Security-constrained by design.
---

# SQL Explore (natural language → read-only SQL)

## What it does
Turns a PM's question ("weekly active teams by signup cohort") into a correct, readable, **read-only** SQL query for the target dialect, with its schema assumptions stated and a one-line plain-English explanation — so PMs can self-serve analytics without waiting on data eng, and without risk.

## When to use / when NOT to use
- **Use** to draft a SELECT query from a question, or to explain/refine an existing query.
- **Do NOT use** to design schema, build pipelines, or analyze a returned dataset (`saas-metrics-diagnosis`).

## Inputs
- **Required:** the question + the dialect (BigQuery / PostgreSQL / MySQL / Snowflake) if known.
- **Optional:** schema/table names, column meanings, date grain. *Unknown table/column names are written as clearly-named placeholders with a "confirm against your schema" note — never asserted as real.*

## Method (methodology)
1. **Restate the question** as a precise, measurable ask (grain, window, filters, dedup).
2. **Map to schema** — real tables/columns if provided, else labeled placeholders.
3. **Write read-only SQL** for the dialect — CTEs for readability, explicit joins, window functions where apt.
4. **Explain in one line** + state the assumptions (what "active" means, timezone, dedup) that change the answer.
5. **Flag misleading traps** — survivorship, double-counting on joins, NULL handling, sampling.

## Output spec
The query (in a code block, read-only), a one-line explanation, stated assumptions, and any "this could mislead because…" caveat.

## Security (enforced — non-negotiable)
- **SELECT / read-only only.** Never generates `INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/GRANT` or any state-changing statement. If the user asks for one, decline and explain this skill is read-only.
- **No credentials/connection strings** in queries; never embeds secrets or hardcoded auth.
- **PII caution:** flags when a query would expose personal data and suggests aggregation/minimization; doesn't `SELECT *` on user tables by default.
- **No raw string interpolation patterns** that model SQL injection; parameterize where a value is user-supplied.

## Sprntly integration (optional)
- **Inputs:** the connected schema from the knowledge graph (so tables/columns are real, not placeholders).
- **Outputs:** the query; results routed to `saas-metrics-diagnosis` for interpretation.
- **Degrades to:** standalone with labeled placeholder schema.

## Quality checklist (the bar)
- [ ] **Read-only** — SELECT only; no state-changing statements, ever.
- [ ] Dialect-correct; readable (CTEs/explicit joins).
- [ ] Schema assumptions stated; placeholders flagged where the real schema is unknown.
- [ ] Misleading-result traps flagged; PII minimized; no secrets embedded.

## Known gaps / limitations
- Correct SQL on a wrong mental model of the data still misleads — confirm column meanings.
- Doesn't run the query or validate against the live schema — it drafts; you verify.

## Worked example
**Input:** "weekly active teams, last 8 weeks, BigQuery." → a read-only CTE query bucketing by `DATE_TRUNC(week)`, deduping teams, with assumptions stated ("active = ≥1 completed loop; UTC weeks") and a caveat about teams created mid-week.
