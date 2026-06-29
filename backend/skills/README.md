# Vendored PM Agent Skills

These are method specs from the **PM-Agent-Skills** library (73 skills across 8
categories), vendored here as the prompt-layer method specs that Sprntly's
agents bind to. Each agent prepends a skill's `SKILL.md` (the *method*) ahead of
its own agent-specific system prompt at call time — see
`app/skills/loader.py` and `app/graph/gateway.py`.

## Vendored-subset policy

We deliberately vendor **only** the skills our live agents actually bind, to keep
this repo lean. We do not mirror the full upstream library. When a new agent
binds a new skill, copy just that skill's directory here (its `SKILL.md` plus any
`modules/`, `templates/`, `scripts/` it needs) and add a loader test.

Category prefixes from upstream (e.g. `03-prioritization-and-planning/`) are
**flattened away** — each skill lives at `skills/<id>/`. The per-skill structure
(`SKILL.md`, `modules/`, `templates/`, `scripts/`) is preserved.

## Currently vendored

| id | upstream category | extras |
|----|-------------------|--------|
| prd-author | 04-definition-and-specs | templates/ |
| prioritize | 03-prioritization-and-planning | scripts/ (scoring math ported to `app/synthesis/scoring.py`) |
| decision-memo | 03-prioritization-and-planning | — |
| public-feedback-report | repo-only (public/external review & social mining) | examples/ |
| interview-synthesis | 01-discovery-and-research | examples/ |
| feedback-synthesis | 01-discovery-and-research | — |
| competitive-intelligence-review | 02-strategy-and-positioning | modules/ (all), templates/ |
| incident-runbook | 05-delivery-and-execution | — |
| business-context | 02-strategy-and-positioning | templates/ (incl. business-context-schema.yaml) |
| fact-check | 01-discovery-and-research | templates/ |
| weekly-brief | 06-stakeholder-and-communication | references/, assets/ (brief composer bound by `app/synthesis/agent.py`) |

Each loaded skill carries a `content_hash` (first 12 hex of the sha256 over all
its files), recorded in the decision log via `prompt_version` so the exact
method version behind any decision is auditable.
