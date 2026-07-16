# Sprntly MCP Server

A customer-facing [Model Context Protocol](https://modelcontextprotocol.io) server: lets a Sprntly customer connect their own AI client (Claude Desktop, Claude Code, claude.ai custom connectors) to **their own** Sprntly workspace — tickets, PRDs, prototypes, evidence, briefs, ideation — the same trust model as the existing OAuth connectors (Google Drive/Figma/Slack/etc.), just inbound instead of outbound. What a token can see is scoped by its **role** (`developer` or `pm`, chosen when the token is minted — see [Token roles](#token-roles)).

## Layout

```
mcp/
├── mcp_server/
│   ├── app.py             # ASGI app factory (FastMCP + Streamable HTTP transport)
│   ├── auth.py            # CompanyContext + contextvar, read by tools
│   ├── middleware.py       # pure-ASGI bearer-token auth wrapper
│   ├── backend_client.py  # async httpx calls to the backend's internal API
│   ├── tools.py            # the MCP tools + token-role gating (PM_ONLY_TOOLS)
│   └── __main__.py         # ASGI entry: `app = create_app()`
├── deploy/
│   ├── sprntly-mcp.service # systemd unit
│   └── setup.sh            # one-shot installer for EC2
└── tests/
```

**Zero database credentials.** This service never touches Supabase directly. Every request carries `Authorization: Bearer <mcp_token>` (minted by a customer from Settings → MCP Access); this server calls the backend's internal (`X-Internal-Key`-gated) API to resolve that token to a `{company_id, user_id, role, token_role}`, then calls the backend's internal data routes — passing `company_id` explicitly — to fetch data. See `backend/app/routes/mcp_tokens.py` (token issuance) and `backend/app/routes/internal_mcp.py` (resolve + data routes).

None of the tools take a `dataset`/`company` parameter — the company scope is resolved once, server-side, from the bearer token, never from client input (one-user-one-company is a schema-enforced invariant on the backend).

## Token roles

A token is minted as **`developer`** or **`pm`** (picked in Settings → MCP Access, immutable after creation; stored as `mcp_tokens.token_role`):

- **developer** — the ticket-centric tool set only: your assigned tickets, their PRDs, prototypes, and evidence.
- **pm** — everything: the developer set plus the workspace-level product surfaces (`list_datasets`, `get_current_brief`, `get_ideation`, `get_latest_prd`).

Tokens minted before roles existed default to `pm` (they keep the full tool set they were created with). Enforcement is two-layer: `RoleScopedFastMCP` (app.py) filters `tools/list` per request so a developer token's client never sees the PM-only tools, and every PM-only tool impl re-checks the role before touching the backend — a client calling a hidden tool anyway gets a refusal, not data.

## Local development

```bash
cd mcp
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest

# Boot against a locally running backend (see backend/README.md)
export BACKEND_URL=http://127.0.0.1:8000
export BACKEND_INTERNAL_KEY=<same value as backend's INTERNAL_API_KEY>
.venv/bin/uvicorn mcp_server.__main__:app --reload --port 8003
```

Or via Docker:

```bash
cd mcp
docker build -t sprntly-mcp .
docker run -d --name sprntly-mcp -p 8003:8003 \
  -e BACKEND_URL=http://host.docker.internal:8000 \
  -e BACKEND_INTERNAL_KEY=<same value as backend's INTERNAL_API_KEY> \
  sprntly-mcp
```

Verify the protocol layer before wiring up a real client:

```bash
npx @modelcontextprotocol/inspector
# point it at http://localhost:8003/mcp with the bearer token from a
# POST /v1/mcp-tokens call against your backend
```

Then add it to a real client — Claude Code: `claude mcp add --transport http sprntly http://localhost:8003/mcp` (pass the token via whatever header/auth config that client version supports), or Claude Desktop's local dev config.

## Production

Lives on the existing EC2 host as a separate systemd unit (`sprntly-mcp.service`) on port 8003, served at **`https://api.sprntly.ai/mcp`** (a `location /mcp` block in `backend/deploy/nginx.conf` — riding the existing api.sprntly.ai cert, so no DNS or certbot work is needed). Deploys via the `.github/workflows/deploy-mcp.yml` GitHub Actions workflow on every push to `main` that touches `mcp/**`.

Zero-touch: the workflow also creates `mcp/.env` on the box on first deploy — `BACKEND_INTERNAL_KEY` is read from the backend's own `.env`, and `MCP_ALLOWED_HOSTS` / `MCP_ALLOW_URL_TOKEN` are seeded with prod defaults. Values already present in the file are never overwritten, so `.env` can be hand-edited later without a deploy clobbering it. `mcp/deploy/setup.sh` remains as a manual fallback for a fresh box.

## Required env

| var                     | who needs it                                          |
| ----------------------- | ------------------------------------------------------ |
| `BACKEND_URL`           | server — required; base URL of the Sprntly backend      |
| `BACKEND_INTERNAL_KEY`  | server — required; must match the backend's `INTERNAL_API_KEY` |
| `MCP_ALLOWED_HOSTS`     | prod — public hostname(s) nginx proxies from (`api.sprntly.ai`), or every proxied request 421s |
| `MCP_ALLOW_URL_TOKEN`   | prod — `1` so the `?token=` connector URLs the Settings UI hands out authenticate |

## Tools

**Both roles (the developer set):**

| tool | what it does |
| --- | --- |
| `list_tickets(status?, ticket_type?)` | The tickets **assigned to the token owner** (assignment is matched on the assignee set in the web app; teammates' and unassigned tickets never appear). When a ticket's PRD syncs with a tracker, each row also carries `tracker_provider` / `tracker_status` / `tracker_url`. |
| `get_ticket(ticket_key)` | Full ticket — generated title, description, acceptance criteria, scope and context (what/why) **merged with any edits**, plus comments and attachments. Everything needed to implement it. Includes `tracker` (provider, status, assignee, url, last_synced_at) when the PRD's tickets sync with ClickUp/Jira — the sync is server-side, automatic, and **two-way**: MCP edits reach the tracker on the next pass, and tracker-side edits/status moves flow back into the ticket (newest edit wins). |
| `get_prd(prd_id)` | The parent PRD for full product context (a ticket's `prd_id` comes from `list_tickets`/`get_ticket`). |
| `list_prd_tickets(prd_id, status?, ticket_type?)` | **All** tickets in one PRD — the full scope across every assignee (deliberately not assignee-scoped). |
| `get_prd_prototype(prd_id)` | The design prototype behind a PRD: status (`generating`/`ready`/`failed`), `is_complete`, preview image, and viewer links — `app_url` (in-app, needs login) always; `public_url` (no-login share link) only if a PM already shared it. Never changes share settings, never exposes the signed bundle URL. |
| `get_prd_evidence(prd_id)` | The research evidence behind the PRD's parent insight (why the PRD exists). Resolved via the PRD's `brief_id` + `insight_index` — the same join the web's Evidence tab uses. Returns `content` + `content_format` (`markdown` or `html`), capped at 150k chars. Read-only, never triggers generation. |
| `update_ticket_fields(ticket_key, ...)` | Update status/priority/title/sprint — assignment is deliberately web-only. |
| `update_ticket_description(ticket_key, ...)` | Replace description; acceptance criteria only when explicitly passed. |
| `add_ticket_comment(ticket_key, body)` | Comment on a ticket, attributed to the token owner. |
| `add_ticket_attachment(ticket_key, label, sub?)` | Link a PR/branch to a ticket. |

**PM tokens only:** `list_datasets`, `get_current_brief` (weekly brief), `get_ideation` (the prioritized ideation shortlist), `get_latest_prd`.

All tools are company-scoped from the token — no `dataset`/`company` parameter. The server also sends FastMCP `instructions` on connect to orient the model on the ticket → PRD → prototype/evidence flow.

Comments are attributed to the **token owner** (resolved server-side from the token's `user_id` → their profile name, else email, else `mcp`) — the client can't post as someone else.

Any token can read **and** write within its role's tool set (roles gate *which* tools, not read vs write).

**Still out of scope:** async-job tools (PRD/brief/evidence generation — these need internal polling to stay synchronous from the client's view), OAuth-based MCP auth (static bearer token is simpler and matches the existing API-key UX bar), per-token read/write scopes, and rate limiting (none exists anywhere in this codebase yet — a leaked token has no request-volume ceiling; track this as a follow-up).
