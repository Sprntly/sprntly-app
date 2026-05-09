# Sprntly

Monorepo for the [Sprntly](https://www.sprntly.ai) demo product (Cursor for product managers).

```
backend/    FastAPI on EC2 — api.sprntly.ai
web/        Next.js demo (added soon) — sprntly.ai/demo via Vercel rewrite
```

## Auto-deploy

Every push to `main` rebuilds:

- **Backend → EC2**: `.github/workflows/deploy-backend.yml` SSHes into EC2, `git pull`s, restarts the systemd service.
- **Frontend → Vercel**: Vercel project watches the `web/` subdir and auto-deploys on every push.

## Quick links

- Backend README: [`backend/README.md`](backend/README.md) (currently the project README, will be split out)
- API base URL: `https://api.sprntly.ai`
- Demo: `https://sprntly.ai/demo` (once the rewrite is in place)
