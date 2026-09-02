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

## Environments (two Supabase projects — prod is fenced off)

Since **2026-09-01** each environment has its own database.

| Environment | Supabase project | Who points at it |
|---|---|---|
| **prod** | `vnfnmiauoblodxmjmaqw` | the `production` branch only |
| **staging** | `ghcpqurzykyymtwtngtx` (us-east-2) | the `main` branch **and every local dev machine** |

Local development and staging share one project on purpose. The line that
matters is the one around **prod**: nothing but a `production` deploy may point
at it, and no laptop ever should.

The workflows resolve the project from `github.ref`:

- **deploy-app** builds the static bundle with `NEXT_PUBLIC_SUPABASE_URL/ANON_KEY`
  from the `*_DEV` secrets on `main` and the prod secrets on `production`. These
  are inlined at build time (`output: "export"`), so a bundle is welded to
  whichever project it was built against — repointing needs a rebuild.
- **deploy-backend**'s migrate job runs `db push` against `SUPABASE_DB_URL_DEV`
  on `main` and `SUPABASE_DB_URL` on `production`.

`~/Sprntly-staging/backend/.env` carries the staging project's
`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
`SUPABASE_JWT_SECRET` and `SUPABASE_DB_PASSWORD`, alongside the app-facing keys
that were already staging-specific (`FRONTEND_URL=https://staging.sprntly.ai`,
`ALLOWED_ORIGINS=staging...`).

### What this changes, and what it costs

**A migration merged to `main` no longer touches customer data.** It applies to
staging, gets exercised there, and reaches prod when `production` deploys. The
corollary is that **prod is not migrated until you promote** — never assume it
is because staging is.

The cost is the thing the shared setup was buying: connectors no longer come
for free on staging. Staging needs its own connector OAuth apps, its own
`TOKEN_ENCRYPTION_KEY`, its own Resend configuration, and its own Auth settings
on the staging project (Site URL, redirect allow-list, SMTP). Anything
connected on prod is no longer visible from staging — that is the point, but it
does mean staging needs its own seed tenant and test accounts.

`TOKEN_ENCRYPTION_KEY` must be **identical on the staging box and on every
local machine**, since both read the same `connections` rows. A mismatch fails
as an opaque Fernet `InvalidToken` on refresh, not as a clear configuration
error. Prod's key is separate and shared with neither.

## Prod safety
Never deploy `production` or touch prod services/DB/DNS without explicit sign-off.
Staging is the safe place to iterate.
