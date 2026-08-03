# Vendored PM Agent Skills

Method specs vendored as the prompt-layer text Sprntly's agents bind to. An
agent prepends a skill's `SKILL.md` (the *method*) ahead of its own
agent-specific system prompt at call time — see `app/skills/loader.py` and
`app/graph/gateway.py`.

## Vendored-subset policy

**Nine skills, and that is a CLOSED set.** This directory used to hold 82. The
other 73 existed to be selected by the chat router, which offered ~78 methods
per turn; chat no longer selects a method at all — it answers directly — so
those skills had no caller left.

Every skill here is bound BY NAME from exactly one place in the code. That is
the admission test: if nothing in `app/` names it, it does not belong here.
`tests/test_skills_catalog.py` pins the set, so adding one back is a decision
rather than a drop-a-folder side effect.

Category prefixes from upstream (e.g. `03-prioritization-and-planning/`) are
**flattened away** — each skill lives at `skills/<id>/`. The per-skill structure
(`SKILL.md`, `modules/`, `templates/`, `references/`, `assets/`) is preserved.

## Currently vendored

| id | bound from | extras |
|----|-----------|--------|
| prd-author | `app/prd_runner.py` (Part A) | templates/, assets/ (prd.css, server-applied) |
| implementation-spec | `app/prd_runner.py` (Part B) | templates/ |
| evidence-brief | `app/evidence_kg.py`, `app/evidence_runner.py` | references/, assets/ (evidence.css) |
| user-stories | `app/stories/generate.py` | — |
| top-insights | `app/synthesis/agent.py` (the weekly brief composer) | references/ (rubric + signal-schema drive its self-critique hard gates), assets/ |
| jira-extraction | `app/kg_ingest/runner.py` (`PROVIDER_SKILLS`) | references/expected-signal-shape.md |
| hubspot-extraction | `app/kg_ingest/runner.py` (`PROVIDER_SKILLS`) | references/expected-signal-shape.md |
| clickup-extraction | `app/kg_ingest/runner.py` (`PROVIDER_SKILLS`) | references/expected-signal-shape.md |
| roadmap-extraction | `app/kg_ingest/roadmap.py` | references/expected-signal-shape.md |

**Never delete the four extraction skills.** They are not PM methodology — they
are per-connector parsing contracts wired at ingest, and
`app/graph/evals.py::SKILL_EXPECTED_VOCAB` is maintained against each one's
`references/expected-signal-shape.md`. Deleting one degrades the knowledge graph
every other feature reasons over.

Each loaded skill carries a `content_hash` (first 12 hex of the sha256 over all
its files), recorded in the decision log via `prompt_version` so the exact
method version behind any decision is auditable.

## What a missing skill does

`loader.get_skill` RAISES `UnknownSkillError` — deliberately, because the nine
bindings above are load-bearing and a silently-empty method is worse than a
stack trace. Tolerance for ids that are NOT vendored lives one layer up, in
`graph.gateway._build_method_prefix` and `llm.call_with_web_search`, which
degrade to running method-less and record `+bare` in `prompt_version`. That is
what lets a research pipeline keep passing `skill=<id>` for attribution after
its method doc went away.
