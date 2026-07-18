# Sprntly App

Monorepo for the production **Sprntly** app (Cursor for product managers). Sister repo: [`Sprntly/sprntly-demo`](https://github.com/Sprntly/sprntly-demo) — the demo surface.

## Domains

| Surface | URL | Source | Served by |
|---|---|---|---|
| **App frontend** | `https://app.sprntly.ai` | `web/` (this repo) | EC2 nginx → `/var/www/sprntly-app/` (static Next export) |
| **App backend** | `https://api.sprntly.ai` | `backend/` (this repo) | EC2 → `sprintly.service` (FastAPI uvicorn :8000) |
| **DS Agent** | `https://api.sprntly.ai/agent/` | `ds-agent/` (this repo) | EC2 → `sprntly-agent.service` (uvicorn :8002) |
| **MCP server** | `https://api.sprntly.ai/mcp` | `mcp/` (this repo) | EC2 → `sprntly-mcp.service` (uvicorn :8003) |
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

## Error tracking (Sentry)

All four services are wired to [Sentry](https://sentry.io) for error tracking. **Every integration is gated on a DSN env var** — with no DSN set (local dev, CI, tests) Sentry is a complete no-op: nothing initialises, nothing is sent. Turn it on per-environment by setting the DSN.

**One-time setup (sentry.io):** create an org, then a project per service (suggested slugs `sprntly-web`, `sprntly-backend`, `sprntly-mcp`, `sprntly-ds-agent`) — each gives you a DSN. Set each service's DSN in its deploy environment.

| Service | Init | DSN var | Other vars |
|---|---|---|---|
| `web/` (browser) | [`web/sentry.client.config.ts`](web/sentry.client.config.ts), wired via `withSentryConfig` in [`web/next.config.ts`](web/next.config.ts) | `NEXT_PUBLIC_SENTRY_DSN` | `NEXT_PUBLIC_SENTRY_ENVIRONMENT`, `NEXT_PUBLIC_SENTRY_RELEASE`, `NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE` |
| `backend/` | [`backend/app/sentry.py`](backend/app/sentry.py) (pydantic `Settings`) | `SENTRY_DSN` | `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`, `SENTRY_TRACES_SAMPLE_RATE` |
| `mcp/` | [`mcp/mcp_server/sentry.py`](mcp/mcp_server/sentry.py) (`os.environ`) | `SENTRY_DSN` | `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`, `SENTRY_TRACES_SAMPLE_RATE` |
| `ds-agent/` | [`ds-agent/ds_agent/sentry.py`](ds-agent/ds_agent/sentry.py) (`os.environ`) | `SENTRY_DSN` | `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`, `SENTRY_TRACES_SAMPLE_RATE` |

Defaults are **errors-only** (`traces_sample_rate = 0`) to avoid perf-monitoring cost; raise the sample rate to enable tracing. PII (request bodies, headers, cookies) is **not** sent.

- **Backend / mcp / ds-agent:** add `SENTRY_DSN=…` (and optionally the others) to each service's `.env` on the server (the systemd `EnvironmentFile`). The FastAPI/Starlette integrations auto-capture unhandled request-handler exceptions.
- **web (static export):** `NEXT_PUBLIC_*` vars are inlined at **build time**, so they must be present in the build environment. For the GitHub Actions `deploy-app.yml` build, add `NEXT_PUBLIC_SENTRY_DSN` (+ optional `NEXT_PUBLIC_SENTRY_ENVIRONMENT`, `NEXT_PUBLIC_SENTRY_RELEASE`). To upload source maps for readable stack traces, also set `SENTRY_ORG`, `SENTRY_PROJECT`, and `SENTRY_AUTH_TOKEN` (build-only — never expose the token to the client). Without the token the build still injects the client SDK; only source-map upload is skipped.

Since the web app is a **static export**, only browser-side errors are captured (there is no server/edge runtime). Next's build prints a benign "Could not find a Next.js instrumentation file" warning for the (unused) server SDK — suppress it with `SENTRY_SUPPRESS_INSTRUMENTATION_FILE_WARNING=1` if desired.

**Session Replay (web):** enabled by default — Sentry records a video-like reproduction (clicks, navigation, DOM, network) of every session that hits an error (`NEXT_PUBLIC_SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE`, default `1.0`) plus a 10% sample of all sessions (`NEXT_PUBLIC_SENTRY_REPLAY_SAMPLE_RATE`, default `0.1`). Recordings are **privacy-first**: all text and input values are masked and media is blocked ([`web/sentry.client.config.ts`](web/sentry.client.config.ts)), so replays capture layout and interaction, never the customer content typed or displayed. Set both rates to `0` to disable recording entirely. Because replay records real user sessions, mention it in your privacy policy. Replays are viewable per-issue and under **Replays** in each web project. Note: Sentry is error/replay tooling, not product analytics — for funnels / click-counts / retention use a dedicated analytics tool (PostHog, Amplitude, …).
