# Ask / Chat — full request→response flow and latency map

**Purpose.** Trace every hop a chat question makes, from the moment a user presses Enter to the moment the answer renders — which endpoints the frontend calls, which sub-calls the backend makes, which of them are serial and blocking, and where the wall-clock time actually goes.

**Method.** Read from the code on `origin/main` at `3a58a33c`. Every claim below cites the file and line that establishes it. Nothing here is measured on production — durations marked *"est."* are estimates derived from the code's own constants and comments; the constants themselves are exact.

---

## 0. Executive summary

A plain chat question ("what are customers complaining about?") makes **3 sequential LLM calls** before the first answer token can exist, plus up to **2 more** in interceptor branches:

| # | Call | Model | On the critical path? | Where |
|---|---|---|---|---|
| 1 | `POST /v1/chat/intent` — action envelope | **sonnet-4-6**, 4k max_tokens | **Yes — blocks the send entirely** | [chat_intent.py:255](../backend/app/chat_intent.py#L255) |
| 2 | Skill router | haiku-4-5, 300 max_tokens | Yes | [qa_agent.py:782](../backend/app/qa_agent.py#L782) |
| 2b | Interception contest (only when the company has custom skills AND the question hits a call-digest gate) | haiku-4-5, 300 max_tokens | Yes, conditionally | [qa_agent.py:600](../backend/app/qa_agent.py#L600) |
| 3 | Answer generation | sonnet-4-6, **12k max_tokens**, streamed | Yes — the dominant leg | [ask_runner.py:1519](../backend/app/ask_runner.py#L1519) |
| 4 | Next-prompt suggestions | haiku-4-5, 600 max_tokens | No — fires after render | [chat_suggestions.py:335](../backend/app/chat_suggestions.py#L335) |

Around those sit **~15–25 Supabase round trips**, **one OpenAI embeddings call**, and — on any question with ≥2 topical keywords — a **live fan-out to every connected connector with an 8-second wall-clock budget**.

**The five biggest structural contributors, ranked:**

1. **`POST /v1/chat/intent` is a full sonnet call that runs before `POST /v1/ask` is even sent** — with no cheap pre-gate, on every non-slash message. It is pure serial dead time in front of the ask. ([§2.1](#21-step-1--post-v1chatintent-the-action-envelope))
2. **`POST /v1/ask` deliberately sleeps 5–7 seconds on a cache hit**, and can block up to **25 more seconds** waiting on a warming cache row — inside the request handler. ([§3.2](#32-cache-resolution--the-deliberate-delay))
3. **The connector sweep adds up to 8 s** of live third-party I/O to the direct-answer path whenever the question has ≥2 topical terms. ([§4.6](#46-the-cross-connector-sweep))
4. **KG retrieval is an N+1**: up to 12 serial `edges_to` queries, one per matched theme, plus a ledger walk. ([§4.7](#47-knowledge-graph-retrieval))
5. **One uvicorn worker, and a process-wide cap of 6 concurrent LLM calls.** Everything above shares one Python process. ([§6](#6-runtime-and-deployment-constraints))

---

## 1. Actors and surfaces

| Layer | Component | File |
|---|---|---|
| Composer / thread UI | `ChatScreen` | [web/app/components/screens/app/ChatScreen.tsx](../web/app/components/screens/app/ChatScreen.tsx) |
| Ask client runner | `runAskGeneration` / `resumeAskGeneration` | [web/app/lib/runAskGeneration.ts](../web/app/lib/runAskGeneration.ts) |
| Poll helper | `pollUntil` | [web/app/lib/poll.ts](../web/app/lib/poll.ts) |
| SSE preview | `subscribeToGenerationStream` | [web/app/lib/streamGeneration.ts](../web/app/lib/streamGeneration.ts) |
| HTTP transport | `request()` in `api.ts` | [web/app/lib/api.ts:68](../web/app/lib/api.ts#L68) |
| Ask route | `POST /v1/ask`, `GET /v1/ask/{id}` | [backend/app/routes/ask.py](../backend/app/routes/ask.py) |
| Background worker | `run_ask_job` | [backend/app/ask_job_runner.py](../backend/app/ask_job_runner.py) |
| Router + interceptors | `qa_agent.answer` | [backend/app/qa_agent.py:1332](../backend/app/qa_agent.py#L1332) |
| Direct answer composer | `compose_ask_answer` | [backend/app/ask_runner.py:1321](../backend/app/ask_runner.py#L1321) |
| LLM gateway | `llm_call` | [backend/app/graph/gateway.py:150](../backend/app/graph/gateway.py#L150) |
| Transport + retries + concurrency gate | `app/llm.py` | [backend/app/llm.py](../backend/app/llm.py) |

---

## 2. Phase A — the frontend, before the ask is even sent

Everything in this phase happens **before** `POST /v1/ask`. The user has already pressed Enter; the thinking bubble is on screen; nothing has been asked yet.

### 2.1 Step 1 — `POST /v1/chat/intent` (the action envelope)

**Gate:** runs for every message that does *not* start with `/`, whenever the `chat_intent_envelope` flag is on. The flag **defaults to on** — [`types.ts:508`](../web/app/lib/onboarding/types.ts#L508) reads `flags?.chat_intent_envelope !== false`, so an absent key means enabled.

**Call site:** [ChatScreen.tsx:3729-3739](../web/app/components/screens/app/ChatScreen.tsx#L3729-L3739). It is `await`ed. Nothing proceeds until it returns.

**Backend work** ([routes/chat.py:59-107](../backend/app/routes/chat.py#L59-L107)):

1. Auth chain — `require_agents_module` → `require_workspace` → `require_company` (see [§3.1](#31-the-auth-and-tenancy-chain))
2. `require_owned_prd` when a PRD is open — 1 DB read
3. `get_conversation_prd_id` fallback — 1 DB read
4. `_load_history` — 2 DB reads (ownership check + all turns)
5. `resolve_chat_intent` → **one `llm_call` on `claude-sonnet-4-6`**, `max_tokens=4000`, carrying a history block clamped to 24 000 chars ([chat_intent.py:65-66, 255-271](../backend/app/chat_intent.py#L255-L271))

**Why this is the single most expensive avoidable leg:**

- It is **sonnet**, not haiku, and it is asked to echo requirement details verbatim into a `task` field — hence `max_tokens=4000`.
- **There is no cheap pre-gate.** `resolve_chat_intent` goes straight to `llm_call`. Compare `_sweep_context`, which checks `len(sweep_terms(question)) < MIN_TERMS` — pure string work — *before* paying for a DB read ([qa_agent.py:1232-1237](../backend/app/qa_agent.py#L1232-L1237)). The intent resolver has no equivalent.
- It **holds one of the 6 process-wide LLM concurrency slots** ([llm.py:171](../backend/app/llm.py#L171)) for its full duration, so it competes with the answer calls of every other user on the box.
- Its own docstring says it is "not on the answer path" ([routes/chat.py:7-9](../backend/app/routes/chat.py#L7-L9)). That is true of `/suggestions`, which fires after render. It is **not** true of `/intent`, which is awaited before the send.

> **Est. contribution: 1.5–5 s of pure serial dead time on every message**, before the ask has begun.

### 2.2 Step 2 — `POST /v1/prd/classify-command` (conditional)

Only when the envelope is off or its fetch failed, *and* the message mentions a PRD without matching the regex ladder. haiku classifier, awaited. [ChatScreen.tsx:3852-3854](../web/app/components/screens/app/ChatScreen.tsx#L3852-L3854).

### 2.3 Step 3 — attachment extraction (conditional)

Per attachment, in parallel ([ChatScreen.tsx:3968-3994](../web/app/components/screens/app/ChatScreen.tsx#L3968-L3994)):

- `POST /v1/ask/extract-file` — server-side document→markdown, no LLM ([routes/ask.py:372](../backend/app/routes/ask.py#L372)); capped at 25 MB
- `attachmentsApi.upload` — original file to storage, best-effort

Extracted text is clamped to 100 000 chars and appended to the question as an `[Attached files]` block.

### 2.4 Step 4 — conversation creation

On a tab's **first** message only, `persistence.ensureConversation` is awaited before the ask POST ([ChatScreen.tsx:4069-4077](../web/app/components/screens/app/ChatScreen.tsx#L4069-L4077)). One `POST /v1/conversations`. Deliberate: without it, first-message HTML reports were captured with `conversation_id = NULL`. Follow-ups resolve from the tab with no round trip.

### 2.5 Transport overhead per call

Every request from `api.ts` ([api.ts:68-95](../web/app/lib/api.ts#L68-L95)) resolves the Supabase access token via `accessTokenProvider()` and attaches `X-Workspace-Id`. This is per request, including every poll.

---

## 3. Phase B — `POST /v1/ask`

[routes/ask.py:224-349](../backend/app/routes/ask.py#L224-L349)

### 3.1 The auth and tenancy chain

`require_agents_module` → `require_workspace` → `require_company`:

| Step | Cost | Cached? |
|---|---|---|
| Supabase JWT decode | JWKS fetch, cached 300 s | ✅ |
| `memberships_for_user` | DB | ✅ 30 s ([authcache.py:66](../backend/app/db/authcache.py#L66)) |
| `profile_name_for_user` | DB | ✅ 60 s |
| `get_workspace` / `ensure_default_workspace` | DB | ✅ 30 s |
| `get_workspace_member` (non-owner/admin only) | DB | ✅ 30 s |
| `feature_flags_for_company` | DB | ❌ **uncached** ([entitlements.py:183](../backend/app/entitlements.py#L183)) |

The `feature_flags` read is uncached and is repeated later in the pipeline — `_ds_claude_enabled` and `_cross_connector_sweep_enabled` each call `read_feature_flags` again ([qa_agent.py:1181, 1211](../backend/app/qa_agent.py#L1181)). **Up to 3 uncached reads of the same row per ask.**

### 3.2 Cache resolution — the deliberate delay

Then, still inside the POST handler:

- `require_owned_dataset` — DB read
- `require_owned_prd` when `prd_id` present — DB read
- `_load_history` — 2 DB reads ([routes/ask.py:126-186](../backend/app/routes/ask.py#L126-L186))
- `_resolve_cache_hit`, run on a worker thread ([routes/ask.py:189-221](../backend/app/routes/ask.py#L189-L221)):

```
CACHE_HIT_DELAY_MIN_SECONDS   = 5.0     # deliberate synthetic delay
CACHE_HIT_DELAY_MAX_SECONDS   = 7.0
GENERATING_POLL_TIMEOUT_SECONDS = 25.0  # blocking wait on a warming row
GENERATING_POLL_INTERVAL_SECONDS = 0.5
```

Two behaviours worth putting in front of the team:

1. **On a cache hit, the handler sleeps a random 5–7 seconds on purpose.** The comment states the reason plainly: *"Pre-warmed cache hits return in <100 ms — instantaneous responses break the demo illusion that the LLM is generating the answer in real time"* ([routes/ask.py:45-50](../backend/app/routes/ask.py#L45-L50)). This is a product decision, not a bug — but it is a guaranteed 5–7 s floor on the fastest possible answer in the product.
2. **On a still-warming row, the handler blocks up to 25 seconds** polling at 0.5 s. Worst case `POST /v1/ask` itself takes **~25–32 s** before returning an `ask_id`.

The cache path is skipped for PRD-tab asks and for any mid-thread ask (a thread that already holds an assistant turn) — [routes/ask.py:273-278](../backend/app/routes/ask.py#L273-L278). In an active conversation, **the cache is effectively never used**, so the 25 s branch only bites on first turns.

### 3.3 Kick-off

`start_ask_job` (DB insert) → `asyncio.create_task(run_ask_job(...))` → return `{ask_id, status:"generating"}`. Strong refs held in `_inflight_tasks` so the task is not GC'd ([routes/ask.py:42](../backend/app/routes/ask.py#L42)).

---

## 4. Phase C — the background worker

[ask_job_runner.py](../backend/app/ask_job_runner.py) → `asyncio.to_thread(_run_sync, ...)`. A heartbeat task bumps `updated_at` so the orphan sweep can't fail a long-but-healthy answer ([ask_job_runner.py:32-57](../backend/app/ask_job_runner.py#L32-L57)).

### 4.1 Question embedding

`ask_runner._question_embedding` — **one OpenAI embeddings HTTP call**, serial, before anything else ([ask_runner.py:1223](../backend/app/ask_runner.py#L1223)). Published on a ContextVar so both document selection and KG retrieval reuse it — exactly one embedding per ask.

### 4.2 The interceptor ladder

`qa_agent.answer` runs a fixed ladder of deterministic interceptors before the router ([qa_agent.py:1492-1789](../backend/app/qa_agent.py#L1492-L1789)). Each is behind a cheap regex gate, but a gate that *matches* pays real I/O:

| # | Interceptor | Gate | Cost when the gate matches |
|---|---|---|---|
| 1 | Call-index listing | `is_listing_request` | `ensure_fresh` → 1 DB read; if stale + source connected, **inline sync, up to 8 s** ([call_index.py:809-863](../backend/app/call_index.py#L809-L863)) |
| 2 | Single named call | `is_single_call_request` | Index lookup + one transcript fetch |
| 3 | Call digest | `is_call_digest` | `_names_live_source` (DB), `_custom_beats_digest` (**haiku call** + DB), `has_call_source` (DB), then a **live multi-call fetch + VoC pass — the comment measures ~168 s and ~$0.23** ([qa_agent.py:1496-1497](../backend/app/qa_agent.py#L1496)) |
| 4 | VoC report | `is_voc_report_request` | same as 3 |
| 5 | DS analysis | `is_data_analysis_request` | filesystem check + **uncached** `read_feature_flags` + the DS engine |
| 6 | Windowed call question | `windowed_call_question` | DB read + possible `ensure_fresh` → digest |
| 7 | Ticket update | `is_ticket_update` | `ticket_update.answer` (tool loop) |
| 8 | Tracker lookup | `is_jira_lookup` | `tracker.any_connected` (DB) + a live read-only tool loop |
| 9 | Named connector lookup | `is_connector_lookup` | live provider read + KG |
| 10 | Unnamed document lookup | `document_lookup_candidates` | `connected_providers` (DB) + live wiki fetch + KG |

`_index_fresh`, `_contest_memo` and `_live_source_memo` are lazily memoised so a question matching *no* gate pays none of this ([qa_agent.py:1396-1490](../backend/app/qa_agent.py#L1396-L1490)).

**Routing text.** Every gate judges `_routing_text(question)` — the user's words up to the first `[Attached files]` marker — never the attachment body ([qa_agent.py:1267-1285](../backend/app/qa_agent.py#L1267-L1285)).

### 4.3 Routing

`qa_agent.route()` ([qa_agent.py:705-883](../backend/app/qa_agent.py#L705-L883)), three tiers:

1. **Slash fast-path** — `/my-skill …`, custom skills only, one DB lookup, no LLM.
2. **Regex fast-path** — `detect_intent`, threshold 0.75. **Terminal (zero LLM) when the company has uploaded no custom skills.** When they have, it degrades to an advisory prior handed to tier 3.
3. **LLM router** — haiku, `max_tokens=300`, `temperature=0`, `prompt_version="qa-router-v7"`. Its input carries the company skill block + the keyword prior + the history block + the question. Its system prompt is deliberately tenant-invariant so one Anthropic cache entry serves every company ([qa_agent.py:289-295](../backend/app/qa_agent.py#L289-L295)).

Before the router runs, `_routing_text_with_filenames` adds two more DB reads — `list_company_files` and `active_conversation_attachment_names` ([qa_agent.py:1288-1321](../backend/app/qa_agent.py#L1288-L1321)).

`on_route` fires the instant the decision resolves and writes `ask_jobs.routed_skill`, so a waiting client can name the running skill mid-generation ([ask_job_runner.py:155](../backend/app/ask_job_runner.py#L155)).

### 4.4 Cancellation checkpoint

`_check_cancelled` polls `is_ask_cancelled(ask_id)` (DB read) immediately after routing — the highest-value stop point, since it saves the sonnet/opus call ([qa_agent.py:1837](../backend/app/qa_agent.py#L1837)).

### 4.5 Path selection

- **Direct** (`decision.skill_id is None`) — the common case → `compose_ask_answer`
- **Custom skill** → `_answer_single_shot` with the uploaded method injected
- **Pipeline** → `public_feedback` / `company_research` / `competitive_intel` / `call_digest`, each with its own web-research sweep (minutes)

### 4.6 The cross-connector sweep

Direct path only, and skipped when a PRD is open ([qa_agent.py:1887](../backend/app/qa_agent.py#L1887)). Order of gates ([qa_agent.py:1229-1256](../backend/app/qa_agent.py#L1229-L1256)):

1. `len(sweep_terms(question)) < MIN_TERMS` (=2) → return "" — pure string work, deliberately first
2. `_cross_connector_sweep_enabled` — global setting + **uncached** per-company flag, default ON
3. `connector_sweep.context_block` — the fan-out

The fan-out ([connector_lookup/sweep.py](../backend/app/connector_lookup/sweep.py)):

```
BUDGET_S         = 8.0                # ONE wall-clock budget across ALL legs
LIVE_PROVIDERS   = jira, clickup, slack, confluence, hubspot
PER_SOURCE_CHARS = 2_500
TOTAL_CHARS      = 12_000
MAX_TERMS        = 8
```

All legs run concurrently in a `ThreadPoolExecutor`; a leg still running at the deadline is abandoned (`shutdown(wait=False)`) and reported as unread. Healthy sources land in ~1.1 s per the module's own measurement; a dead connector costs the full 8 s.

> **Est. contribution: ~1 s typical, 8 s worst case**, on any question with ≥2 topical words.

### 4.7 Knowledge-graph retrieval

`retrieve_context` ([graph/retrieval.py:194-455](../backend/app/graph/retrieval.py#L194-L455)):

1. Reuse the shared embedding (no second embed)
2. `find_candidates(type="theme")` — pgvector kNN, `k=12`
3. Noise floor drop
4. **One `edges_to` query per surviving theme — serial, up to 12 round trips**, then one batched `get_signals` ([retrieval.py:352-370](../backend/app/graph/retrieval.py#L352-L370))
5. `active_signals` — 1 query
6. `load_session_context` + `_ledger_entities` × 3 (decisions / hypotheses / outcomes), each resolving labels
7. Rank + cap to `DEFAULT_TOKEN_BUDGET = 2200`

Step 4 is the remaining N+1. The per-signal fetch was already batched; the per-theme edge walk was not.

### 4.8 Document grounding

`document_grounding` ([ask_runner.py:647-1042](../backend/app/ask_runner.py#L647-L1042)) — runs on every path, including PRD-grounded asks:

- `list_company_files` — DB
- `_owned_conversation_attachments` — DB (ownership-checked)
- `_catalog_documents` — DB
- **Stage N** — documents the question *names* (substring, binary, no tunable)
- **Stage T** — `find_catalog_candidates`: a **pgvector RPC** fusing a tsvector lexical channel and a semantic kNN by reciprocal rank fusion, `k=25`. **No similarity floor** — it always returns candidates when the catalog is non-empty ([ask_runner.py:267-275](../backend/app/ask_runner.py#L267-L275))
- **Body resolution** — up to `MAX_SELECTED_DOCUMENTS` (3) bodies. Uploads read from DB; **Confluence pages are fetched live** ([ask_runner.py:890-922](../backend/app/ask_runner.py#L890-L922))
- Char budget `_DOCUMENT_CHAR_BUDGET = 24 000`, split evenly across selected docs

### 4.9 Prompt assembly and the answer call

`compose_ask_answer` ([ask_runner.py:1321-1607](../backend/app/ask_runner.py#L1321-L1607)) assembles:

| Slot | Contents | Cacheable? |
|---|---|---|
| `system` | `ASK_SYSTEM` + KG addendum + sweep addendum + `today_line()` + `connected_sources_line()` (1 DB read) | cached when >1000 chars |
| `user_cacheable_prefix` | `facts` → corpus/PRD block → `docs_block` | **prefix-cached** |
| `user` | history block + KG/sweep context + the question | never cached |

**The prefix ordering is load-bearing.** `docs_block` is volatile on every ask (it carries per-question "[loaded for this question]" markers), so it is placed **last** in the cacheable prefix. Putting it first invalidated the shared corpus cache on every single ask ([ask_runner.py:1490-1499](../backend/app/ask_runner.py#L1490-L1499)).

Then the answer call:

```
model      = claude-sonnet-4-6   (claude-opus-4-7 for competitive-intelligence-review)
max_tokens = 12_000
stream     = True when on_delta is wired
timeout    = LONG_REQUEST_TIMEOUT_S (600 s) when streaming, else 120 s
```

Wrapped by `_create_with_retries` ([llm.py:277-373](../backend/app/llm.py#L277-L373)):

- **Concurrency gate first** — `_llm_gate.acquire()`, 6 slots process-wide, held for the *whole* call including retries. A call queuing ≥5 s logs a saturation warning.
- **4 attempts**, backoff `0.5 × 4^n × jitter` ≈ 0.5 s + 2 s + 8 s worst case
- Retryable: connection error, timeout, 429, ≥500
- On a retry the stream restarts from zero and the JSON extractor is rewound

Finally: `log_agent_decision` (DB insert), `log_ask` (DB insert), `complete_ask_job` (DB update), `capture_report` (no-op for markdown answers), `token_stream.close(channel, kind="done")`.

---

## 5. Phase D — how the client gets the answer back

Two channels run **in parallel**, and only one is authoritative.

### 5.1 The poll (authoritative)

`GET /v1/ask/{ask_id}` every **1500 ms**, wall-clock budget **12 minutes** ([runAskGeneration.ts:40-59](../web/app/lib/runAskGeneration.ts#L40-L59)):

```
POLL_INTERVAL_MS   = 1500
MAX_MS             = 12 * 60 * 1000
TRANSIENT_RETRIES  = 4
TRANSIENT_BACKOFF  = 400 ms × (attempt+1)
```

`pollUntil` measures elapsed time with `Date.now()`, not a tick count, and wakes immediately on `visibilitychange` — so a backgrounded tab (setTimeout throttled to ~1/min) still times out correctly and catches up on refocus ([poll.ts](../web/app/lib/poll.ts)).

Each poll pays the full auth chain. Nearly all of it is cached (30–60 s TTLs), and `GET /v1/ask/{id}` uses `require_workspace` — **not** `require_agents_module` — so it avoids the uncached feature-flags read. Over a 60 s answer that is ~40 polls.

**Terminal states:** `ready` → answer; `cancelled` → treated as a stop, no error bubble; `error` → error bubble; still `generating` at 12 min → `AskTimeoutError`, and the pending id is deliberately **left in place** so a reload re-attaches.

### 5.2 The SSE token stream (display only)

`GET /v1/ask/{ask_id}/stream` ([routes/ask.py:494-530](../backend/app/routes/ask.py#L494-L530)). EventSource can't send headers, so the bearer rides as `?token=` (`require_workspace_from_query`).

Frames: an optional `replay` catch-up (for a tab re-attaching mid-answer), then `{kind:"delta",text}`, then `{kind:"done"|"error"}`. The client throttles preview re-renders to ~7/s (`PARTIAL_THROTTLE_MS = 150`).

Three important properties:

- **Progressive display only.** The poll stays authoritative.
- **Not every path streams.** Only the two schema-shaped paths (direct answer, single-shot skill answer) publish deltas. Pipelines — call digest, public feedback, competitive intelligence, DS analysis, tracker lookup — return non-streamable payloads and arrive whole via the poll ([qa_agent.py:1352-1359](../backend/app/qa_agent.py#L1352-L1359)). **On those paths the user watches a static skeleton for the entire run.**
- **Single-worker transport.** `token_stream` is in-process. On a multi-worker deployment it yields nothing and the poll silently carries the whole result.

A stream that drops *after* delivering at least one delta triggers `onStreamDrop` → a "live preview dropped, still generating" note. A stream that never delivered a delta stays silent, because that is indistinguishable from a skill that simply doesn't stream ([runAskGeneration.ts:22-35](../web/app/lib/runAskGeneration.ts#L22-L35)).

### 5.3 After the answer renders

1. `POST /v1/conversations/{id}/turns` — persist the assistant turn
2. **then** `POST /v1/chat/suggestions` — haiku, 600 max_tokens, awaits the turn write first so the backend reads a complete thread ([ChatScreen.tsx:4161-4179](../web/app/components/screens/app/ChatScreen.tsx#L4161-L4179))

Neither blocks the answer.

### 5.4 Resume and stop

- **Resume** — the active `ask_id` is persisted per tab in localStorage (`jobResume`). On mount, `resumeAskGeneration` re-attaches by id without re-POSTing.
- **Stop** — `POST /v1/ask/{id}/cancel` flips the row to `cancelled`; `qa_agent` polls that between LLM steps and raises `AskCancelled` **before** the expensive call.

---

## 6. Runtime and deployment constraints

Two facts that bound everything above:

**One uvicorn worker.** `ExecStart=… uvicorn app.main:app --host 127.0.0.1 --port 8000` — no `--workers` ([deploy/sprintly.service:15](../backend/deploy/sprintly.service#L15)). Every ask worker runs as `asyncio.to_thread` inside that single process. This is *load-bearing*, not an oversight: `token_stream` is an in-process pub/sub, so adding workers would silently break SSE previews for every user whose poll lands on a different worker than their generation.

**Six concurrent LLM calls, process-wide.** `_llm_gate` caps at `LLM_MAX_CONCURRENCY` (default 6), with at most `LLM_BG_CAP` (default 1) held by pre-warming ([llm.py:60-114, 171](../backend/app/llm.py#L60-L114)). The gate is a threading primitive acquired from worker threads, so it never blocks the event loop — but it *does* mean:

> With the intent envelope on, **each concurrent user occupies 2–3 of those 6 slots in sequence** (intent → router → answer). Roughly **2–3 simultaneous chatters saturate the box**, after which every further call queues and logs `"LLM call waited …s for a concurrency slot — model calls are saturated"`.

**nginx** is configured correctly for this: `proxy_buffering off` and `proxy_read_timeout 600s` on the api block ([deploy/nginx.conf:82-95](../backend/deploy/nginx.conf#L82-L95)). It is not the bottleneck.

---

## 7. Consolidated latency budget — a plain question, direct path

Serial unless marked parallel. Durations marked *est.* are inferred from the code's constants and comments, not measured on prod.

| # | Leg | Type | Est. |
|---|---|---|---|
| 1 | `POST /v1/chat/intent` — **sonnet, 4k tokens** | LLM | **1.5–5 s** |
| 2 | `POST /v1/conversations` (first message only) | DB | 0.1–0.3 s |
| 3 | `POST /v1/ask` — auth + ownership + history + `start_ask_job` | 6–8 DB | 0.3–0.8 s |
| 3b | …cache hit → **deliberate sleep** | sleep | **5–7 s** (cache hits only) |
| 3c | …warming row → blocking poll | sleep | up to **25 s** (rare) |
| 4 | Question embedding | OpenAI HTTP | 0.2–0.5 s |
| 5 | Interceptor ladder (no gate matched) | regex | <0.05 s |
| 5b | …`ensure_fresh` when a call gate matched + index stale | sync | up to **8 s** |
| 6 | `_custom_skill_block` + filenames + `detect_intent` | 3 DB | 0.2–0.5 s |
| 7 | Skill router — **haiku, 300 tokens** | LLM | **0.6–1.5 s** |
| 8 | Cross-connector sweep | parallel HTTP | **~1 s, 8 s worst** |
| 9 | Document grounding — catalog + RRF RPC + ≤3 bodies | DB + pgvector + HTTP | 0.4–1.5 s |
| 10 | KG retrieval — kNN + **≤12 serial `edges_to`** + ledger | 15–20 DB | **0.8–2.5 s** |
| 11 | `company_facts_block` + `connected_sources_line` + corpus | 3 DB + FS | 0.2–0.5 s |
| 12 | **Answer — sonnet, 12k max_tokens, streamed** | LLM | **6–25 s** |
| 13 | Decision log + `log_ask` + `complete_ask_job` | 3 DB | 0.2 s |
| 14 | Poll interval quantisation | poll | **+0–1.5 s** |
| | **Typical total, no cache hit** | | **≈ 12–35 s** |
| | **Add a cache hit** | | **+5–7 s floor** |
| | **Add a dead connector** | | **+8 s** |
| | **Add a stale call index** | | **+8 s** |

**Time before the first streamed token appears** — legs 1 through 11 — is roughly **4–12 s** on a healthy box, during which the user sees only a skeleton. On a pipeline path (call digest, competitive intelligence, public feedback) nothing streams at all and the wait runs to **minutes**.

---

## 8. Where the time is going — findings, ranked

*This section is diagnosis only. No changes have been made; each item is a candidate for the team to decide on.*

### F1 — `POST /v1/chat/intent` is a serial sonnet call in front of every message

Sonnet-4-6, `max_tokens=4000`, no cheap pre-gate, occupies an LLM slot, and blocks the send. Enabled by default. On any message that is plainly a question, its verdict is `answer` — i.e. the fallback — so the call bought nothing.

*Directions worth evaluating:* a lexical pre-gate that skips the call for question-shaped messages (the sweep's `MIN_TERMS` gate is the local precedent); haiku instead of sonnet; a lower `max_tokens`; or firing it **concurrently with** `POST /v1/ask` and cancelling the ask if a non-`answer` intent comes back.

### F2 — the 5–7 s synthetic cache-hit delay

Deliberate and documented ([routes/ask.py:45-50](../backend/app/routes/ask.py#L45-L50)). It converts the product's fastest possible answer into its slowest floor. Worth an explicit product decision now that the surface is used for real work rather than demos — and note it is already bypassed when the client actually waited on a warming row (`waited_on_generation`).

### F3 — the 25 s in-handler block on a warming cache row

`GENERATING_POLL_TIMEOUT_SECONDS = 25.0` holds the POST open. The client already has a poll-and-resume architecture that could carry this wait without holding the request.

### F4 — three uncached reads of one `feature_flags` row per ask

`require_agents_module`, `_ds_claude_enabled` and `_cross_connector_sweep_enabled` each re-read it. Every other per-request tenancy read is TTL-cached in `authcache`; this one is not.

### F5 — the KG `edges_to` N+1

Up to 12 serial round trips, one per matched theme ([retrieval.py:352-370](../backend/app/graph/retrieval.py#L352-L370)). The per-signal fetch below it was already batched; the edge walk was not.

### F6 — an 8 s worst case from a single dead connector

The sweep's shared budget bounds it correctly, but a connector that is expired or slow costs the full budget on **every** qualifying question until someone fixes it. There is no cooldown after a timeout, and no signal surfaced to the user or an operator.

### F7 — 6 LLM slots against 2–3 calls per user turn

At 2–3 slots per concurrent chatter, the box saturates at roughly 2–3 simultaneous users. `LLM_MAX_CONCURRENCY` is env-tunable; the constraint is RAM (the comment measures ~80 MB extra for 6 concurrent streams against a ~3.8 GB footprint).

### F8 — one uvicorn worker, and SSE is why

Adding `--workers` is not a free win: `token_stream` is in-process, so it would silently disable live previews. Multi-worker needs a shared transport (Redis pub/sub or equivalent) first.

### F9 — the pipeline paths don't stream at all

Call digest, public feedback, competitive intelligence, company research, DS analysis and tracker lookup all return whole payloads. On runs the code itself measures at ~168 s (digest) and multiple minutes (CIR), the user watches a static skeleton. `on_phase` labels exist and are wired for CIR ([qa_agent.py:1963](../backend/app/qa_agent.py#L1963)) but are not emitted by the other pipelines.

### F10 — poll interval quantisation

`POLL_INTERVAL_MS = 1500` adds an average ~750 ms and up to 1.5 s between a job finishing and the client seeing it. The SSE `done` frame lands first and could wake the poll immediately; today it only ends the preview.

---

## 9. Appendix — endpoint inventory for one chat turn

| Order | Method | Endpoint | Blocking? | Backend cost |
|---|---|---|---|---|
| 1 | POST | `/v1/chat/intent` | **Yes** | auth + 4 DB + **sonnet** |
| 2 | POST | `/v1/ask/extract-file` (per attachment) | Yes | auth + document parse (no LLM) |
| 2b | POST | `/v1/attachments/upload` (per attachment) | No | storage write |
| 3 | POST | `/v1/prd/classify-command` (conditional) | Yes | auth + haiku |
| 4 | POST | `/v1/conversations` (first message) | Yes | auth + DB insert |
| 5 | POST | `/v1/ask` | Yes | auth + 6 DB + cache resolution (**0 s / 5–7 s / ≤25 s**) |
| 6 | GET | `/v1/ask/{id}/stream` (SSE) | No | auth + in-process subscribe |
| 7 | GET | `/v1/ask/{id}` × N, every 1.5 s | No | auth (cached) + 1 DB |
| 8 | POST | `/v1/conversations/{id}/turns` | No | auth + DB insert |
| 9 | POST | `/v1/chat/suggestions` | No | auth + 2 DB + haiku |
| 10 | POST | `/v1/ask/{id}/cancel` (on Stop) | No | auth + DB update |

---

*Generated from `origin/main` @ `3a58a33c`, 2026-08-07.*
