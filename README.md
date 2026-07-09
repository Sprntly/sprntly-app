# Sprntly App

Monorepo for the production **Sprntly** app (Cursor for product managers). Sister repo: [`Sprntly/sprntly-demo`](https://github.com/Sprntly/sprntly-demo) — the demo surface.

## Domains

| Surface | URL | Source | Served by |
|---|---|---|---|
| **App frontend** | `https://app.sprntly.ai` | `web/` (this repo) | EC2 nginx → `/var/www/sprntly-app/` (static Next export) |
| **App backend** | `https://api.sprntly.ai` | `backend/` (this repo) | EC2 → `sprintly.service` (FastAPI uvicorn :8000) |
| **DS Agent** | `https://api.sprntly.ai/agent/` | `ds-agent/` (this repo) | EC2 → `sprntly-agent.service` (uvicorn :8002) |
| **MCP server** | `https://mcp.sprntly.ai/mcp` | `mcp/` (this repo) | EC2 → `sprntly-mcp.service` (uvicorn :8003) |
| **Marketing** | `https://sprntly.ai`, `https://www.sprntly.ai` | [`davidkmumuni-lab/sprntlyai_website`](https://github.com/davidkmumuni-lab/sprntlyai_website) | Vercel |

The demo surface (`demo.sprntly.ai` / `api.demo.sprntly.ai`) lives in [`sprntly-demo`](https://github.com/Sprntly/sprntly-demo) and runs its own backend process — independent JWT secret, independent SQLite DB, no shared session state.

## Repo layout

```
backend/    FastAPI — deploys to api.sprntly.ai (sprintly.service on EC2)
web/        Next.js — deploys to app.sprntly.ai (static export rsync'd to EC2 nginx)
ds-agent/   Data-science chat agent — deploys to api.sprntly.ai/agent (sprntly-agent.service)
mcp/        Customer-facing MCP server — connect your own AI client (Claude Desktop/Code,
            claude.ai) to your workspace's tickets/PRDs/prototypes/evidence, gated by
            role-scoped bearer tokens minted in Settings → MCP Access (see mcp/README.md)
supabase/   DB schema migrations (shared Supabase project `vnfnmiauoblodxmjmaqw`)
```

## Auto-deploy

Every push to `main` is built and deployed by GitHub Actions:

| Workflow | Trigger | Result |
|---|---|---|
| `.github/workflows/deploy-app.yml` | `web/**` | Builds Next static export → rsyncs to EC2 `/var/www/sprntly-app/` → smoke-tests `app.sprntly.ai` |
| `.github/workflows/deploy-backend.yml` | `backend/**` | SSH → EC2 `~/Sprntly` → `git reset --hard origin/main` → pip install → restart `sprintly.service` → 15s healthcheck on `api.sprntly.ai/healthz` |
| `.github/workflows/deploy-agent.yml` | `ds-agent/**` | SSH → restart `sprntly-agent.service` → healthcheck on `api.sprntly.ai/agent` |
| `.github/workflows/deploy-mcp.yml` | `mcp/**` | SSH → reload nginx → restart `sprntly-mcp.service` → healthcheck on `:8003/health` |
| `.github/workflows/sync-backend-env.yml` | manual (`workflow_dispatch`) | One-shot env-var upserter for the backend `.env` on EC2 |

## API base URL

The frontend defaults to `https://api.sprntly.ai`. Override locally with `NEXT_PUBLIC_API_URL` in `web/.env.local`.
