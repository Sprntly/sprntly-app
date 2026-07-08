# MCP v2: Developer tools for coding-editor clients

## Context

The customer-facing MCP server (`mcp/`) currently exposes 11 tools covering the basic ticket loop (list/get tickets, get PRD/brief/backlog, update fields/description, comment, attach). The user wants to expand it for **developers connecting from a coding editor** (Claude Code, Cursor). Exploration confirmed the backend already has service functions for everything below — the work is thin internal routes (`X-Internal-Key`-gated, `company_id` injected server-side) + thin MCP tools + tests. All four capability groups were approved.

**Invariants to preserve:** never accept `company_id`/dataset from the MCP client (always from the token's `CompanyContext`); id-keyed resources go through ownership guards (`require_owned_prd`, etc. in `backend/app/deps/ownership.py`); writes are attributed to the token owner (`user_id` → `display_name_for_user`, `backend/app/db/companies.py:164`).

## New tools (10) + 1 extension

| Tool | Backend service (exists) |
|---|---|
| `get_impl_spec(prd_id?)` — machine-readable "Part B" spec | `ensure_impl_spec` (`backend/app/prd_runner.py:556`), returns `{llm_part, cached}` |
| `list_tickets(+sprint?, assignee?, mine=False)` — extension | widen edits select in `internal_mcp.py` tickets route |
| richer `get_ticket` — add blocked_by/blocks, story_points, data_gaps, prd_section, ears_ids, signals, ac_inherited, route | already in base story dict (`app/stories/generate.py:232`); just add to route response |
| `list_prd_questions(prd_id?)` | `list_questions` (`app/db/prd_input_questions.py:72`) |
| `answer_prd_question(question_id, answer)` — patches the PRD | `apply_answer` (`app/prd_questions.py:200`) + flow mirrored from `routes/prd.py:412-471` |
| `get_business_context()` | `load_business_context(company_id).render_for_prompt()` (`app/business_context.py:307/209`) |
| `ask(question, conversation_id?)` + `get_ask_result(ask_id)` | `start_ask_job`/`get_ask_job` (`app/db/asks.py:133/178`), flow from `routes/ask.py` |
| `list_pull_requests()` | `list_open_pull_requests(company_id)` (`app/db/github.py:286`) |
| `list_clickup_lists()` | `clickup_oauth.list_lists` + `_clickup_access_token` (`app/stories/push.py:52`) |
| `push_tickets_to_clickup(list_id, ticket_keys?)` | `push_stories_to_clickup` (`app/stories/push.py:68`) — needs small `ids=` param (below) |

## Phase 1 — Backend: new/extended internal routes

All in [backend/app/routes/internal_mcp.py](backend/app/routes/internal_mcp.py) on `data_router`, `Depends(_require_internal_key)`, `company_id: str` query param, lazy imports (file convention):

1. **Enrich `GET /tickets/{key}/data`**: add the 9 missing Story fields listed above (additive, straight from `story.get(...)`).
2. **Extend `GET /tickets`**: optional `sprint`, `assignee` (case-insensitive match on assignee dict's `display_name`/`email`), `assignee_user_id` (exact match on `assignee["user_id"]` — what `mine` uses; assignee shape is `{user_id, display_name, email, role, avatar_url}`). Add `sprint`/`assignee` to returned rows.
3. **`POST /prd/impl-spec`** (`prd_id` optional → default to `latest_prd_for_dataset(slug_for_company_id(company_id))`): `require_owned_prd`, `ensure_impl_spec(prd_id)`, `RuntimeError` → 404. POST because cache miss generates.
4. **`GET /prd/questions`** (`prd_id` optional, same defaulting): `{"prd_id", "questions": list_questions(prd_id)}`.
5. **`POST /prd/questions/{question_id}/answer`** (`user_id` query param, body `{answer}`): derive `prd_id` from the question row → `require_owned_prd`; mirror `routes/prd.py:412-471` (apply_answer → save_prd_version best-effort → update_prd_content → answer_question); `answered_by = display_name_for_user(user_id) or "mcp"`. Return `{ok, question, sections_changed, summary}` — not the full PRD.
6. **`GET /business-context`**: `load_business_context(company_id)`; None → 404; return `{"context": doc.render_for_prompt(), "version": doc.version}`.
7. **`POST /ask`** (async def; body `{question (3..2000), conversation_id?}`): slug derived server-side; cached-ask short-circuit **without** the 5-7s human-facing synthetic delay in `_resolve_cache_hit`; else `start_ask_job` + `asyncio.create_task(run_ask_job(...))` with a module-level strong-ref set (copy `routes/ask.py:31` pattern) and the `"pytest" in sys.modules` inline branch (`routes/ask.py:225`). Returns `{ask_id, status}`.
8. **`GET /ask/{ask_id}`**: 404 if missing or `row["company_id"] != company_id`; same shape as `GET /v1/ask/{id}`.
9. **`GET /github/pull-requests`**: trim rows to `{repo_full_name, pr_number, title, author, html_url, is_draft, body_excerpt, pr_created_at, pr_updated_at}`.
10. **`GET /clickup/lists`**: `ClickUpNotConnectedError` → 404 `clickup_not_connected`.
11. **`POST /clickup/push`** (body `{list_id, ticket_keys?}`): load company's base stories + `ticket_edits`, merge overrides (edited title/description/AC/priority win) before `Story.from_dict`; unknown keys → `"skipped"`; empty → 400. Return `{created, errors, skipped}`.

**Small change in [backend/app/stories/push.py](backend/app/stories/push.py)**: add optional `ids: Sequence[str] | None = None` to `push_stories_to_clickup` so the `clickup_task_map` idempotency key is the **original ticket_key**, not `story.stable_id()` (which changes when edits alter title/body → would duplicate tasks on re-push). Default preserves existing-caller behavior exactly.

## Phase 2 — MCP server

- [mcp/mcp_server/backend_client.py](mcp/mcp_server/backend_client.py): add `timeout: float | None = None` kwarg to `get_json`/`request_json` (default `_TIMEOUT`=30s). Long calls: impl-spec 180s, answer 120s, clickup push 120s.
- [mcp/mcp_server/tools.py](mcp/mcp_server/tools.py): 10 new `_*_impl` coroutines + `@mcp.tool()` registrations following the existing pattern (`require_current_company()` → `get_json`/`request_json` with injected `company_id` (+`user_id` where attributed) → None/404 → friendly `{"message": ...}`). Extend `list_tickets` with `sprint`/`assignee`/`mine` (`mine=True` sends `assignee_user_id=ctx.user_id`; `mine`+`assignee` together → message, no backend call).
- **`ask` polling**: constants `ASK_POLL_INTERVAL_SECONDS=2.0`, `ASK_WAIT_BUDGET_SECONDS=50.0` (monkeypatchable). POST, then poll `GET /ask/{id}` in short calls; `ready` → full answer; budget exhausted → `{status: "generating", ask_id, message: "call get_ask_result(...)"}`. Job keeps running server-side.
- [mcp/mcp_server/app.py](mcp/mcp_server/app.py) `_INSTRUCTIONS` rewrite: find work (`list_tickets` mine/sprint) → understand (`get_ticket`, `get_prd`, `get_impl_spec`, `get_business_context`) → unblock (`list_prd_questions`/`answer_prd_question` — answering edits the PRD) → ask the workspace (`ask`/`get_ask_result`) → work it (update/comment/attach, `list_pull_requests`) → ship (`list_clickup_lists` → `push_tickets_to_clickup`). Keep the "never pass a company/dataset id" sentence.
- [mcp/README.md](mcp/README.md): update tools list; drop the "async-job tools out of scope" line.

## Phase 3 — Tests

- **backend/tests/test_routes_internal_mcp.py** (extend existing helpers): every new path added to the 401-without-key test; impl-spec cached path (seed `llm_part` + matching `llm_part_source_hash`; no LLM call), latest-PRD defaulting, foreign prd → 404; questions list + answer flow (monkeypatch `apply_answer`; assert `answered_by` from seeded profile and `"mcp"` fallback, 409 empty PRD, 502 on RuntimeError); business-context 200/404; ask pytest-inline branch + cached short-circuit + foreign-company 404; github trimmed shape + tenancy; clickup not-connected 404, override-merge push, **idempotency after title edit** (second push → `update_task`, same map key), unknown keys skipped.
- **push.py**: default `ids=None` behavior byte-identical; explicit `ids` keys the map.
- **mcp/tests/test_tools.py**: per tool — passthrough (path/params incl. `company_id`/`user_id` injection), friendly-message-on-None, fail-closed without context (parametrize into existing test); `mine=True` param mapping; ask polling with interval patched to 0 (generating→generating→ready sequence, budget-exhausted path, error path); stubs record `timeout` kwarg for the three long calls.

## Verification

1. `cd backend && pytest tests/test_routes_internal_mcp.py` + full backend suite; `cd mcp && .venv/bin/pytest` (or `python -m pytest`).
2. Rebuild + recreate **both** containers (backend first): `docker build -t sprntly-backend ./backend` / `-t sprntly-mcp ./mcp`, then `docker rm -f` + `docker run` with the same flags used today (`--network sprntly-local`, `--env-file`, `-e BACKEND_URL=http://sprntly-backend:8000 -e MCP_ALLOW_URL_TOKEN=1 -e MCP_DISABLE_DNS_REBINDING_PROTECTION=1` for mcp).
3. Smoke end-to-end with a real token: `npx @modelcontextprotocol/inspector` → `http://localhost:8003/mcp` → call `get_impl_spec`, `list_prd_questions`, `ask`, `list_pull_requests`, `list_clickup_lists` and one `push_tickets_to_clickup` + re-push (verify update-not-duplicate in ClickUp).
4. Deploy note: merge as one PR (backend + mcp together); tools degrade gracefully against an old backend (404 → friendly message); never deploy mcp-only.

## Commit breakdown (one PR, three commits)

1. Backend read-side: ticket enrichment + filters, impl-spec, questions list, business-context, github PRs + tests.
2. Backend write/async-side: answer question, ask kick/status, clickup lists/push + `ids=` param + tests.
3. MCP server: client timeout kwarg, 10 tools + `list_tickets` extension, `_INSTRUCTIONS`, README, mcp tests.
