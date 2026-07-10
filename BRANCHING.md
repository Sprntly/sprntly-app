# Branching & Deploys

Two long-lived branches, two stacks on the same EC2 box.

| Branch | Environment | App | API | Deploys on |
|--------|-------------|-----|-----|------------|
| `main` | **staging** | https://staging.sprntly.ai | https://api.staging.sprntly.ai | every push to `main` |
| `production` | **prod** | https://app.sprntly.ai | https://api.sprntly.ai | every push to `production` |

## Flow

1. PRs merge into `main` (as before). Merging auto-deploys **staging**.
2. Verify on `staging.sprntly.ai`.
3. Promote: fast-forward / merge `main` → `production`. Pushing `production`
   auto-deploys **prod**. "What's on prod" == `production` HEAD.

```
# promote current main to prod (from a clean checkout)
git fetch origin
git push origin origin/main:production      # fast-forward production to main
```

## Services (same box, per-env ports)

| Service | Prod (`production`) | Staging (`main`) |
|---------|--------------------|------------------|
| backend | `sprintly.service` :8000, `~/Sprntly` | `sprintly-staging.service` :8010, `~/Sprntly-staging` |
| ds-agent | `sprntly-agent.service` :8002 | `sprntly-agent-staging.service` :8012 |
| mcp | `sprntly-mcp.service` :8003 | `sprntly-mcp-staging.service` :8013 |
| app (static) | `/var/www/sprntly-app/` | `/var/www/sprntly-app-staging/` |

The four deploy workflows (`deploy-backend`, `deploy-app`, `deploy-agent`,
`deploy-mcp`) each trigger on both branches and resolve the target env from
`github.ref`.

## Environments (DB is isolated; most secrets still shared)

Staging runs against its **own Supabase project** — `sprntly-dev`
(`ghcpqurzykyymtwtngtx`, us-east-2) — separate from prod
(`vnfnmiauoblodxmjmaqw`). `~/Sprntly-staging/backend/.env` is a **real file**
(prod's `.env` with the Supabase/DB/DATA_DIR/FRONTEND_URL/ALLOWED_ORIGINS keys
overridden for dev). The workflows pick the project per branch:

- **deploy-app** builds the static bundle with `NEXT_PUBLIC_SUPABASE_URL/ANON_KEY`
  = prod on `production`, `*_DEV` on `main`.
- **deploy-backend** migrate job runs `db push` against `SUPABASE_DB_URL` on
  `production`, `SUPABASE_DB_URL_DEV` on `main` — staging migrations never touch
  the prod schema.

What this buys us: no prod-data pollution, no cross-env migration bleed, and
background schedulers (weekly brief, connector refresh) run against the empty
dev DB instead of double-firing on prod.

Still **shared** for now (the "other secrets shared" scope): Anthropic/OpenAI
keys, connector OAuth apps, Resend, `TOKEN_ENCRYPTION_KEY`, `JWT_SECRET`. So:

- **Connector-connect (OAuth) is not staging-correct yet** — the OAuth apps'
  redirect URIs are registered for prod hosts. `FRONTEND_URL=https://staging.sprntly.ai`
  in the staging `.env` fixes dev *auth* email/redirect links (dev Supabase auth),
  but third-party connector OAuth needs its own apps to be fully isolated.
- `ALLOWED_ORIGINS` in the staging `.env` = `staging.sprntly.ai` + localhost;
  `COOKIE_DOMAIN=.sprntly.ai` covers it.

### Phase 3 (full isolation) — later
Give staging its own connector OAuth apps + Resend + `TOKEN_ENCRYPTION_KEY`.
Also configure the dev Supabase project's Auth (Site URL, redirect allow-list,
SMTP) so signup/login emails work on staging.

## Prod safety
Never deploy `production` or touch prod services/DB/DNS without explicit sign-off.
Staging is the safe place to iterate.
