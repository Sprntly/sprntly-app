# Sprntly MCP Server

A customer-facing [Model Context Protocol](https://modelcontextprotocol.io) server: lets a Sprntly customer connect their own AI client (Claude Desktop, Claude Code, claude.ai custom connectors) to **their own** Sprntly workspace — briefs, PRDs, tickets, backlog — the same trust model as the existing OAuth connectors (Google Drive/Figma/Slack/etc.), just inbound instead of outbound.

## Layout

```
mcp/
├── mcp_server/
│   ├── app.py             # ASGI app factory (FastMCP + Streamable HTTP transport)
│   ├── auth.py            # CompanyContext + contextvar, read by tools
│   ├── middleware.py       # pure-ASGI bearer-token auth wrapper
│   ├── backend_client.py  # async httpx calls to the backend's internal API
│   ├── tools.py            # the 5 v1 MCP tools
│   └── __main__.py         # ASGI entry: `app = create_app()`
├── deploy/
│   ├── sprntly-mcp.service # systemd unit
│   └── setup.sh            # one-shot installer for EC2
└── tests/
```

**Zero database credentials.** This service never touches Supabase directly. Every request carries `Authorization: Bearer <mcp_token>` (minted by a customer from Settings → MCP Access); this server calls the backend's internal (`X-Internal-Key`-gated) API to resolve that token to a `{company_id, user_id, role}`, then calls the backend's internal data routes — passing `company_id` explicitly — to fetch data. See `backend/app/routes/mcp_tokens.py` (token issuance) and `backend/app/routes/internal_mcp.py` (resolve + data routes).

None of the 5 tools take a `dataset`/`company` parameter — the company scope is resolved once, server-side, from the bearer token, never from client input (one-user-one-company is a schema-enforced invariant on the backend).

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

Lives on the existing EC2 host as a separate systemd unit (`sprntly-mcp.service`) on port 8003. Nginx (`backend/deploy/nginx.conf`) proxies `api.sprntly.ai/mcp` to it. Deploys via the `Deploy mcp to api.sprntly.ai/mcp` GitHub Actions workflow on every push to `main` that touches `mcp/**`.

First-time setup on a fresh box:

```bash
# ssh ec2-user@<instance>
cd ~/Sprntly/mcp
# Create .env with BACKEND_URL and BACKEND_INTERNAL_KEY
sudo bash deploy/setup.sh
```

## Required env

| var                     | who needs it                                          |
| ----------------------- | ------------------------------------------------------ |
| `BACKEND_URL`           | server — required; base URL of the Sprntly backend      |
| `BACKEND_INTERNAL_KEY`  | server — required; must match the backend's `INTERNAL_API_KEY` |

## Tools

**Read:** `list_datasets`, `get_current_brief`, `get_backlog`, `get_latest_prd`, `get_prd(prd_id)`, `list_tickets(status?, ticket_type?)`, `get_ticket(ticket_key)`.

**Write (tickets):** `update_ticket_fields` (status/priority/title/sprint — assignment is deliberately web-only), `update_ticket_description` (description + acceptance criteria), `add_ticket_comment`, `add_ticket_attachment` (link a PR/branch).

The ticket tools let a developer work a ticket from their coding editor:
- `list_tickets` discovers tickets with their current status (optionally filtered by `status`/`ticket_type`).
- `get_ticket` returns the full ticket — the generated title, description, acceptance criteria, scope and context (what/why) **merged with any edits**, plus comments and attachments — i.e. everything needed to implement it.
- `get_prd` pulls the parent PRD for full product context (a ticket's `prd_id` comes from `list_tickets`/`get_ticket`).
- `update_ticket_fields` / `update_ticket_description` / `add_ticket_comment` / `add_ticket_attachment` update status, edit content, note progress, or link a PR.

All tools are company-scoped from the token — no `dataset`/`company` parameter. The server also sends FastMCP `instructions` on connect to orient the model on this flow.

Comments are attributed to the **token owner** (resolved server-side from the token's `user_id` → their profile name, else email, else `mcp`) — the client can't post as someone else.

Any token can read **and** write (no separate read-only vs read-write scopes in this version).

**Still out of scope:** async-job tools (PRD/brief/evidence generation — these need internal polling to stay synchronous from the client's view), OAuth-based MCP auth (static bearer token is simpler and matches the existing API-key UX bar), per-token read/write scopes, and rate limiting (none exists anywhere in this codebase yet — a leaked token has no request-volume ceiling; track this as a follow-up).
