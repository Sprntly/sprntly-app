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

## Shared `.env` (for now — INTENTIONAL, has sharp edges)

Staging shares prod's Supabase project and secrets: `~/Sprntly-staging/backend/.env`
is a **symlink** to `~/Sprntly/backend/.env`. Consequences until we split envs:

- **Staging writes to the prod database.** Test data lands in prod; destructive
  tests hit prod rows. Be careful.
- **DB migrations auto-apply to the shared (prod) schema the moment they hit
  `main`** — before the prod code that uses them ships. Keep migrations additive
  / backward-compatible.
- **OAuth callbacks & auth emails point at prod** (`FRONTEND_URL=https://app.sprntly.ai`,
  redirect URIs registered for `app.sprntly.ai`). Connector-connect and signup-email
  flows won't be staging-correct until staging gets its own `.env`.
- `ALLOWED_ORIGINS` includes `https://staging.sprntly.ai` so the staging frontend
  can call the staging API. `COOKIE_DOMAIN=.sprntly.ai` covers both.

### Phase 2 (env split) — later
Give staging its own Supabase project + `.env` (own `SUPABASE_*`, `FRONTEND_URL`,
OAuth apps/redirect URIs). Replace the symlink with a real file; no workflow
changes needed.

## Prod safety
Never deploy `production` or touch prod services/DB/DNS without explicit sign-off.
Staging is the safe place to iterate.
