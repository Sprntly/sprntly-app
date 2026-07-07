# Sprntly MCP Server — Design & Rollout Plan

> Status: **Proposal / plan** · Owner: TBD · Target surface: `mcp.sprntly.ai`
> This document describes how we'd expose Sprntly over the **Model Context
> Protocol (MCP)** so any MCP-capable AI client (Claude Desktop, Claude Code,
> Cursor, ChatGPT desktop, etc.) can query a company's Sprntly knowledge graph,
> read its PM artifacts, and drive its PM workflows — with the same tenancy and
> auth guarantees the web app enforces today.

---

## 1. What "an MCP for Sprntly" means

There are two directions MCP could take. They are not mutually exclusive, but
they solve different problems:

| Direction | One-liner | Priority |
|---|---|---|
| **A. Sprntly *as* an MCP server** | External AI clients talk to Sprntly's brain — "use Sprntly from inside Claude/Cursor." | **Primary.** This is the product bet. |
| **B. Sprntly *as* an MCP client** | Sprntly's own agents consume third-party MCP servers instead of the bespoke pullers in `app/kg_ingest/pullers/`. | Secondary / future. |

This plan is about **Direction A**: a hosted Sprntly MCP server. Direction B is
sketched briefly in §10.

**The pitch for A:** Sprntly's differentiator is a per-company knowledge graph
(signals from ClickUp, HubSpot, GitHub, Fireflies…) plus ~73 vendored PM skills
and an artifact factory (PRDs, stories, evidence, prototypes, briefs). Today all
of that is reachable only through `app.sprntly.ai`. An MCP server makes the same
capabilities reachable from wherever the PM already works — so a PM can ask
"what's blocking the checkout redesign?" inside Claude Desktop and get an answer
grounded in *their* company's graph, then say "draft the PRD" and have it land
back in Sprntly.

---

## 2. Why this is unusually cheap to build here

Sprntly already has the two hardest pieces of an MCP server:

1. **A tool registry that is already MCP-shaped.**
   `backend/app/agent_tools/registry.py` stores tools as
   `{ "name", "description", "input_schema" }` — byte-for-byte the shape MCP's
   `tools/list` expects — and exposes `list_tools()` + `dispatch()`. The
   existing `/v1/agent/chat-with-tools` route already runs a tool-use loop over
   it. **MCP `tools/list` ≈ `registry.list_tools()`; MCP `tools/call` ≈
   `registry.dispatch()`.** We wrap, we don't rewrite.

2. **A resolved, single-source tenancy model.**
   `app/auth.py::require_company` turns a Supabase user JWT into a
   `CompanyContext(company_id, role, user_id)` via a pure membership lookup
   (one company per user, product invariant of 2026-06-04). **The client never
   passes a tenant id** — so no MCP tool needs a `company_id` argument, and
   there is no cross-tenant argument to spoof. `GraphFacade` already enforces
   isolation on every read/write.

The net: the MCP server is mostly an **adapter** — protocol translation + auth
bridging + a curated tool surface — on top of plumbing that already exists.

---

## 3. The three MCP primitives, mapped to Sprntly

MCP servers expose three kinds of thing. Sprntly has a natural fit for all
three, which is what makes this a *good* MCP citizen rather than just "REST over
a new transport."

### 3.1 Tools (model-invoked actions)

Curated, PM-facing verbs. Start with a **read-heavy, high-signal** set and add
writes deliberately. Proposed v1 surface (~18 tools):

**Query / retrieval (safe, ship first):**
- `sprntly_ask` — the flagship. Wraps `POST /v1/ask`: ask a question over the
  corpus + knowledge graph, optionally skill-directed. This alone delivers most
  of the value.
- `sprntly_search_graph` — search KG entities / signals / relationships
  (via `GraphFacade`). Returns nodes + provenance.
- `sprntly_get_brief` — current or past weekly brief + its insights
  (`GET /v1/brief/current`, `/{brief_id}`).
- `sprntly_list_artifacts` — list generated PRDs / prototypes / evidence
  (`GET /v1/artifacts`).
- `sprntly_get_prd` / `sprntly_list_prds` — read PRDs (`GET /v1/prd/{id}`,
  `/latest`).
- `sprntly_get_stories` — stories for a PRD (`GET /v1/stories/for-prd/{id}`).
- `sprntly_get_backlog` — backlog items (`GET /v1/backlog`).
- `sprntly_get_ticket` — external tracker ticket + comment summary
  (`GET /v1/tickets/{key}/data`, `/comments/summary`).
- `sprntly_get_evidence` — provenance trail for an insight
  (`GET /v1/evidence/{id}`).
- `sprntly_get_metrics` — product metric series (`GET /v1/metrics/series`).
- `sprntly_get_kpi_tree` / `sprntly_get_roadmap` — company goals & roadmap
  (`GET /v1/company/kpi-tree`, `/roadmap-doc`).
- `sprntly_connector_status` — integration health (`GET /v1/connectors/status`).

**Generation / action (gate behind confirmation; §6):**
- `sprntly_generate_prd` — draft a PRD (`POST /v1/prd/generate` /
  `/generate-from-backlog`). **Async** — see §5.
- `sprntly_generate_stories` — user stories from a PRD
  (`POST /v1/stories/generate`). Async.
- `sprntly_run_research` — competitor / market research
  (`POST /v1/research/competitors/run`, `/market/run`). Async.
- `sprntly_push_stories` — push stories to ClickUp (`POST /v1/stories/push`).
  **Outward write** — always explicit, never bundled with generation.
- `sprntly_comment_on_ticket` — `POST /v1/tickets/{key}/comments`.

Deliberately **excluded from v1:** OAuth `/authorize`·`/callback` redirect
routes (browser-only), everything under `/internal/*` (separate shared-key
auth), destructive deletes, and team/invite management.

### 3.2 Resources (app-driven, addressable read-only data)

Resources let a client attach Sprntly content as context without a tool call.
Natural URI schemes:

- `sprntly://brief/current` and `sprntly://brief/{id}`
- `sprntly://prd/{id}` (and `sprntly://prd/latest`)
- `sprntly://roadmap` · `sprntly://kpi-tree`
- `sprntly://evidence/{id}`
- `sprntly://entity/{id}` — a KG node with its neighbourhood

These map to the same GET handlers as the read tools. Resources are the "@-mention
your PRD into the chat" experience; tools are the "go do a search" experience.
Ship a small set; expand once clients that surface resources well are common.

### 3.3 Prompts (user-invoked templates) — the sleeper feature

**Sprntly's ~73 PM skills map almost 1:1 onto MCP prompts.** The catalog already
exists (`app/skills/catalog.py::build_manifest()`, exposed at
`GET /v1/ask/skills`) with a display label + slash trigger per skill. Surfacing
each routable skill as an MCP prompt means a PM in Claude Desktop types
`/sprntly:prioritize` or `/sprntly:prd-critique` and gets that skill's method —
run against *their* graph — as a first-class slash command in their own client.

Implementation: generate the MCP `prompts/list` dynamically from
`build_manifest()`, filtering to `routable` skills. Each prompt's arguments come
from the skill's frontmatter; invoking it calls into the same skill runner
`/v1/ask` uses. This is a large, differentiated surface for near-zero marginal
cost.

---

## 4. Architecture & where it runs

### 4.1 Transport & framework

- **Transport: Streamable HTTP** (the current MCP standard remote transport,
  superseding the old HTTP+SSE split). This suits a hosted multi-tenant SaaS and
  plays well behind the existing EC2 nginx.
- **Framework:** the Python **MCP SDK / `FastMCP`**, mounted as an ASGI sub-app.
  Because the backend is FastAPI (also ASGI), we can either mount the MCP app
  under the existing service or run it as its own process. **Recommendation: a
  separate service** `sprntly-mcp.service` behind `mcp.sprntly.ai`, importing the
  backend package so it reuses `registry`, `GraphFacade`, `auth`, and the route
  handlers directly (no HTTP hop, no double auth). This mirrors how `ds-agent`
  runs as its own uvicorn service today.

```
MCP client (Claude Desktop / Cursor / Claude Code)
        │  Streamable HTTP + OAuth bearer
        ▼
mcp.sprntly.ai   ──►  sprntly-mcp.service (FastMCP / uvicorn :8003)
                          │  in-process imports
                          ├─ app.agent_tools.registry   (tools/list, tools/call)
                          ├─ app.auth                    (JWT → CompanyContext)
                          ├─ app.graph.facade            (tenant-scoped KG)
                          ├─ app.skills.catalog          (prompts/list)
                          └─ app.routes.* handlers        (reused, not re-HTTP'd)
```

- **Deploy** mirrors `deploy-agent.yml`: a `deploy-mcp.yml` that SSHes to EC2,
  `git reset --hard`, pip install, restart `sprntly-mcp.service`, healthcheck.
- **Local/stdio shim (optional, phase 4):** a thin stdio MCP server (Python or
  a tiny Node package `@sprntly/mcp`) that just proxies to `mcp.sprntly.ai` for
  power users who prefer a local process. Same tools, different transport.

### 4.2 Adapter layer

One module, `app/mcp/server.py`, does four things:

1. **Tool exposure** — iterate `registry.list_tools()`, filter to an MCP
   allow-list (we do *not* auto-expose every internal tool), register each with
   FastMCP. `tools/call` → `registry.dispatch()` with the resolved company /
   installation injected server-side (never from tool args — see §6).
2. **Resource exposure** — register the URI schemes in §3.2 against read handlers.
3. **Prompt exposure** — build from `build_manifest()`.
4. **Auth bridging** — resolve the inbound OAuth token to a `CompanyContext`
   and stash it in request scope so every tool/resource is tenant-bound.

Crucially, several existing tools (e.g. the GitHub agent tools) take an
`installation_id` that the *route layer* resolves and ownership-checks before
dispatch (see `chat_with_tools`, which 404s if the installation doesn't belong
to the company). The MCP adapter must reproduce that resolution — resolve
`installation_id` from the caller's company, **never** accept it as a tool
argument.

---

## 5. The async problem (and how to hide it)

Sprntly's generate/research/story endpoints are **fire-and-forget**: POST
returns an id in a `generating` state; the client polls GET until `ready`. MCP
tool calls, by contrast, want a single return value.

Two ways to model each async capability — pick per tool:

- **Blocking wrapper (preferred for short jobs, e.g. PRD/stories):** the tool
  starts the job and internally polls until `ready` or a timeout, then returns
  the finished artifact. MCP's Streamable HTTP supports **progress
  notifications**, so we stream "generating…" progress while we wait. Cleanest
  UX; bounded by a sane server-side timeout.
- **Start + poll pair (for long jobs, e.g. deep research, prototypes):**
  `sprntly_run_research` returns a `job_id` immediately; a companion
  `sprntly_check_job(job_id)` reports status/result. The model learns to poll.

Default to blocking-with-progress; fall back to start+poll only where jobs
routinely exceed the request budget.

---

## 6. Security & tenancy (the part we must not get wrong)

This is a multi-tenant server handing an AI agent write access to a company's
product data. Non-negotiables:

1. **Tenant identity comes from the token, never from a tool argument.** The
   OAuth bearer resolves to exactly one `company_id` via `require_company`
   semantics. No tool accepts `company_id`, `installation_id`, `enterprise_id`,
   or any other cross-tenant selector. This closes the class of lateral-access
   bugs the codebase has already had to patch (PR #230 on connector routes; the
   installation-ownership guard in `chat_with_tools`).
2. **Reuse `GraphFacade`** for all KG access — it enforces isolation on every
   op. Never hand-roll a query that bypasses it.
3. **Read/write split with confirmation.** Read tools run freely. Write/outward
   tools (`sprntly_push_stories`, `sprntly_comment_on_ticket`, and arguably the
   generators) should be marked as non-idempotent / destructive so clients can
   prompt the user before executing, and should be gated behind an explicit
   scope (see §7).
4. **Scoped tokens.** Issue MCP credentials with scopes (`read`, `generate`,
   `push`) so a user can grant a read-only connection to a less-trusted client.
5. **Rate limiting & quotas.** Reuse `/v1/ask/usage`-style accounting; an agent
   in a loop can call `sprntly_ask` far faster than a human. Per-company and
   per-token limits.
6. **Audit.** Log every tool call with `(company_id, user_id, tool, args-digest,
   result-status)`. Writes especially.
7. **Prompt-injection awareness.** KG content is partly ingested from external
   sources (tickets, transcripts, PRs). When that content flows back to a
   client's model via `sprntly_ask`, it can carry injected instructions. Return
   KG/tool content wrapped/marked as untrusted data, and document that writes
   should require human confirmation — don't let an injected string in a
   HubSpot note trigger `sprntly_push_stories` unattended.

---

## 7. Authentication for MCP clients

MCP standardizes on **OAuth 2.1** for remote servers (authorization-server
metadata discovery per RFC 8414, protected-resource metadata per RFC 9728,
PKCE, dynamic client registration). Sprntly already authenticates users with
**Supabase**, which *is* an OAuth/OIDC provider — so we're well-positioned.

Phased approach:

- **Phase 1 — Personal Access Tokens (fastest path to a working server).**
  Add a "Connect an AI assistant" panel in Sprntly settings that mints a
  long-lived, scoped PAT bound to `(user_id → company_id)`. The MCP server
  accepts it as a bearer and resolves it through the same membership lookup as a
  Supabase JWT. Users paste the token into their client's MCP config. Simple,
  revocable, no OAuth dance. **Ship v1 on this.**
- **Phase 2 — Full OAuth 2.1.** Make the MCP server a proper OAuth resource
  server that delegates to Supabase as the authorization server, so clients that
  support MCP's OAuth flow (Claude Desktop, etc.) get a one-click "Sign in with
  Sprntly" browser flow with no copy-paste. This is the polished, no-token-paste
  experience and where we want to land.

Either way, the server maps the credential → `CompanyContext` and everything
downstream is unchanged. Legacy app/demo cookie sessions are **rejected** (no
user identity → no company), which is correct.

---

## 8. Phased delivery plan

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 — Spike (1–2 days)** | Stand up `FastMCP` in-process, expose a single `sprntly_ask` tool, auth via a hardcoded PAT for one test company. Point Claude Desktop at it. | A real question answered from a real company's graph, end-to-end, from Claude Desktop. |
| **1 — Read-only MVP** | PAT auth (§7 phase 1) + `mcp.sprntly.ai` service + `deploy-mcp.yml`. Ship the ~12 read tools (§3.1) and 4–5 resources (§3.2). Audit logging + rate limits. | External client can query briefs, PRDs, backlog, tickets, metrics, and run `sprntly_ask`, scoped correctly to the caller's company. Cross-tenant access test passes (a token for company A cannot see company B). |
| **2 — Skills as prompts** | Dynamic `prompts/list` from `build_manifest()`; wire prompt invocation to the skill runner. | `/sprntly:prioritize` and friends work as slash commands in a client, run against the caller's graph. |
| **3 — Writes & generation** | Add generators (`generate_prd`, `generate_stories`, `run_research`) with blocking-progress or start+poll (§5), plus outward writes (`push_stories`, `comment_on_ticket`) behind the `generate`/`push` scopes and destructive-hint flags. | A PM can go question → PRD → stories → push to ClickUp entirely from their AI client, with confirmations on writes. |
| **4 — OAuth + stdio shim + polish** | Full OAuth 2.1 via Supabase (§7 phase 2); optional `@sprntly/mcp` stdio proxy; a public server manifest / listing; docs. | One-click connect in Claude Desktop; published setup docs; listed where MCP servers are discovered. |

---

## 9. Open questions to resolve before Phase 1

- **Scope granularity:** is `read` / `generate` / `push` the right split, or do
  we want per-connector scopes (e.g. "can push to ClickUp")?
- **Which skills are safe as prompts?** `build_manifest()` already marks
  `routable`; confirm none leak internal-only behaviour when driven by an
  external model.
- **Job timeouts:** what's the p95 latency of PRD/story generation? Sets the
  blocking-vs-poll cutoff in §5.
- **Multi-seat companies:** a company has many users; MCP tokens are per-user.
  Do writes need to record the acting user for attribution in ClickUp pushes?
  (Yes — `CompanyContext.user_id` is available; thread it through.)
- **Pricing/quota:** should MCP usage draw from the same quota as in-app `/ask`,
  or its own bucket?

---

## 10. Appendix — Direction B (Sprntly as an MCP *client*)

Instead of (or alongside) exposing Sprntly, Sprntly's ingestion layer
(`app/kg_ingest/pullers/*`) could consume **third-party MCP servers** as sources.
Where a vendor ships an official MCP server (e.g. a project tracker or CRM), we
could ingest through it rather than maintaining a bespoke puller — fewer custom
integrations, more sources. This is a maintenance/coverage play, not a product
surface, and is lower priority than Direction A. Worth revisiting once the
inbound MCP server is live and the team has MCP muscle memory.

---

## 11. TL;DR

- Build a **hosted MCP server at `mcp.sprntly.ai`** that exposes Sprntly to any
  AI client. It's cheap because `agent_tools/registry.py` is already MCP-shaped
  and `require_company` already resolves tenancy from the token.
- Map the three MCP primitives: **tools** = curated PM verbs (ask, read
  artifacts, generate, push), **resources** = addressable PRDs/briefs/roadmap/KG
  nodes, **prompts** = the ~73 PM skills as slash commands. The skills→prompts
  mapping is the standout, near-free, differentiated feature.
- **Auth:** start with scoped Personal Access Tokens, graduate to OAuth 2.1 via
  Supabase. Tenant identity always from the token, never a tool arg.
- **Ship read-only first** (Phase 1), then skills-as-prompts, then gated
  writes/generation, then OAuth polish.
