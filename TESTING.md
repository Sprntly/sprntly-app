# Testing & regression prevention

This repo has a large test suite (≈250 backend + ≈180 web + runtime + ds-agent).
The problem we're solving is **not** "too few tests" — it's that regressions in
*existing* features slip through when a new feature lands, and get caught late.
This doc is the plan for closing that gap. Phase 0 is done; Phases 1–3 are the
roadmap.

## Why regressions were slipping through

1. **Green tests didn't block a ship.** `deploy-backend.yml` / `deploy-app.yml`
   trigger on push-to-main independently and did not require the test workflows
   to pass. Gating lived only in GitHub branch protection, which wasn't fully
   enforced.
2. **Everything is mocked, so the seams aren't tested.** Supabase is a
   hand-maintained SQLite mirror (`backend/tests/conftest.py::_FAKE_SCHEMA`);
   LLM + connectors are faked. Each side is verified in isolation; nothing checks
   that backend + real DB + frontend actually agree. Most breaks live here.
3. **Schema drift is silent.** `_FAKE_SCHEMA` must be hand-synced to
   `supabase/migrations/`. Drift → tests green, prod 500s.
4. **No coverage visibility.** No `pytest-cov`, no threshold — the holes are
   invisible.
5. **Loose frontend types + a dead suite.** `web` had no standalone `typecheck`;
   `ds-agent` had tests that no workflow ran.

## The test lanes

| Lane | Command | Runs on | Gate? |
|------|---------|---------|-------|
| Backend fast | `pytest -m "not integration"` (`test-backend.yml`) | every PR/push | **required** |
| Backend integration | full suite incl. `real_build` | PRs→main, push→main, nightly | **required** on PRs→main |
| Web | `vitest run` + `tsc --noEmit` + `next build` (`test-web.yml`) | web/** + backend template paths | **required** |
| ds-agent | `pytest tests/` (`test-agent.yml`) | ds-agent/** | **required** |
| prototype-runtime | `vitest run` (`prototype-runtime.yml`) | prototype-runtime/** | **required** |
| Security | `scan-malware.sh` (`security-guard.yml`) | everything | **required** |

Markers (`backend/pytest.ini`): `integration` (real network/fs) and `real_build`
(spawns real `vite`/`tsc`). The fast lane excludes both. A meta-test
(`test_meta_integration_discipline.py`) enforces that any test spawning real
subprocesses carries the right marker.

## Mocking model (backend)

- **Supabase** → `FakeSupabaseClient` (in-memory SQLite) via `reset_fake_db`.
- **LLM** → `fake_llm` fixture patches `app.llm.call_json`. Reaching the real
  LLM requires the `integration` marker.
- **Connectors** (Slack/GitHub/HubSpot/Google) → `unittest.mock`; an autouse
  fixture no-ops background sync so no test hits a provider.
- **Browser/Playwright** → autouse fixture degrades instead of launching Chromium.
- **Egress** → `app/net_guard.py` blocks network in extractor/adapter tests.

## Phase 0 — done (this PR)

Turns the tests we already have into an actual gate.

- **Deploy backstops.** `deploy-backend` now runs the fast lane before it can
  migrate or restart; `deploy-app` runs vitest + typecheck before shipping the
  bundle; `deploy-agent` runs the ds-agent suite before deploying. A red suite
  can't reach prod even on a direct push to main.
- **ds-agent in CI.** New `test-agent.yml` runs its server tests on every
  ds-agent change (previously run by nothing).
- **Frontend typecheck.** New `web` `typecheck` script (`tsc --noEmit`), run on
  the web fast lane and the deploy gate.

### Required: branch protection on `main`

The deploy backstops are belt-and-braces; the **primary** gate is branch
protection, which is a GitHub setting (not in this repo). Set these on `main`:

- Require a pull request before merging.
- Require status checks to pass, and mark these **required** (the job names):
  `pytest-fast`, `pytest-integration` (test-backend), `vitest` (test-web —
  covers typecheck + vitest + build), `pytest-agent` (test-agent),
  `malware-scan` (security-guard).
- Require branches to be up to date before merging.
- Disallow force-push / deletion of `main`; (recommended) include administrators.

These test workflows have had the `paths:` filter removed from their
`pull_request` trigger **on purpose** — a required check that doesn't run
(because the PR didn't touch its paths) leaves the PR stuck "pending" forever.
Running on every PR guarantees each required check reports.

Apply via CLI:

```bash
./scripts/ci/set-branch-protection.sh          # Sprntly/sprntly-app, main
# toggles: ENFORCE_ADMINS=false  REQUIRE_REVIEWS=1
```

or the GitHub UI: Settings → Branches → Branch protection rules.

`prototype-runtime`'s `test` job is intentionally left path-filtered and
**not** required (requiring it would deadlock PRs that don't touch it). Promote
it later by dropping its `pull_request` paths filter, then adding it to the
script's `CONTEXTS`.

## Phase 1 — close the seams (next)

Where the regressions actually happen.

- **Schema-drift guard.** Stand up an ephemeral Postgres in CI
  (`services: postgres`), apply `supabase/migrations/` for real, run a contract
  subset against it. Kills `_FAKE_SCHEMA` drift permanently.
- **Front↔back API contract.** Generalize the existing `:::block` contract test
  (`test_pipeline_contract.py` ↔ `web/app/lib/__tests__/pipeline-contract.test.ts`):
  snapshot FastAPI's OpenAPI schema, check frontend types against it. Any
  response-shape change fails unless both sides update.
- **E2E smoke (Playwright).** ~10–15 critical-path flows (sign in → create
  company → generate PRD → chat Q&A → connect a connector → weekly brief). We
  have zero E2E today; this is the biggest gap for cross-feature breakage.

## Phase 2 — visibility

- `pytest-cov` + vitest coverage. Enforce **diff coverage** (changed lines must
  be covered) rather than a global %, and ratchet up. This mechanizes the
  "every PR ships tests / backfill when you touch old code" rule.
- Coverage delta as a PR comment.

## Phase 3 — discipline

- **Regression-test-on-bug:** every prod bug gets a *failing test first*, then
  the fix.
- PR template with a "tests added / seams touched" checkbox.
