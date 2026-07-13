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

## Environments (DB SHARED with prod; app URL per-env)

**Staging is repointed at the PROD Supabase project** (`vnfnmiauoblodxmjmaqw`) —
same DB, same encrypted connector tokens, same OAuth callbacks — so connectors
Just Work on staging without registering separate OAuth apps. Speed of iteration
is the priority over environment isolation right now. The separate `sprntly-dev`
project (`ghcpqurzykyymtwtngtx`, us-east-2) still exists but is **no longer wired
in** (the `*_DEV` secrets are dormant).

`~/Sprntly-staging/backend/.env` is a **real file**: prod's Supabase URL/keys
(`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
`SUPABASE_DB_PASSWORD`) with only the app-facing keys kept staging-specific
(`FRONTEND_URL=https://staging.sprntly.ai`, `ALLOWED_ORIGINS=staging...`). The
workflows use the prod project on BOTH branches:

- **deploy-app** builds the static bundle with `NEXT_PUBLIC_SUPABASE_URL/ANON_KEY`
  = the prod project on both `main` and `production` (only `NEXT_PUBLIC_API_URL`
  differs per env — staging points at `api.staging.sprntly.ai`).
- **deploy-backend** migrate job runs `db push` against `SUPABASE_DB_URL` (prod)
  on both branches; idempotent, so a staging deploy applying a migration is a
  no-op when prod later ships the same commit.

What this buys us: connectors, PRDs, and all data are shared, so anything
connected on prod is immediately usable on staging (and vice versa).

Trade-offs (accepted for now): **no prod-data isolation** — staging writes land
in the prod DB, and background schedulers (weekly brief, connector refresh) run
against prod data from whichever env has `SCHEDULER_ENABLED`. Shared too:
Anthropic/OpenAI keys, connector OAuth apps, Resend, `TOKEN_ENCRYPTION_KEY`,
`JWT_SECRET`, `COOKIE_DOMAIN=.sprntly.ai`.

### Re-isolating staging (full isolation) — later
Point the staging `.env` + the deploy workflows back at the `sprntly-dev`
project (restore the `github.ref == production && prod || *_DEV` split in
deploy-app / deploy-backend, and swap the four Supabase keys in
`~/Sprntly-staging/backend/.env` back to the dev values — backups saved as
`~/Sprntly-staging/backend/.env.bak-repoint-*`). Then give staging its own
connector OAuth apps + Resend + `TOKEN_ENCRYPTION_KEY`, and configure the dev
Supabase project's Auth (Site URL, redirect allow-list, SMTP).

## Prod safety
Never deploy `production` or touch prod services/DB/DNS without explicit sign-off.
Staging is the safe place to iterate.
