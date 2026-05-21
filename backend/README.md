# Sprntly Backend

FastAPI backend for [Sprntly](https://www.sprntly.ai). Deployed to AWS EC2 at `api.sprntly.ai`. Called by the Next.js demo at `sprntly.ai/demo`.

## Stack

- Python 3.12 + FastAPI + Uvicorn
- Anthropic Python SDK (Claude API)
- nginx reverse proxy + systemd on Amazon Linux 2023
- SQLite for cached briefs, evidence, PRDs, and Ask responses

## Local development

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes pytest
cp .env.example .env                  # fill in ANTHROPIC_API_KEY, DEMO_PASSWORD
uvicorn app.main:app --reload
```

Then `curl http://127.0.0.1:8000/healthz`.

Run tests: `python -m pytest tests/`.

## Endpoints

- `GET /healthz` — liveness probe
- `POST /v1/auth/login`, `POST /v1/auth/logout`, `GET /v1/auth/me`
- `GET  /v1/datasets` — list registered datasets
- `POST /v1/datasets` — create dataset `{slug, display_name}`
- `POST /v1/datasets/{slug}/files` — multipart upload (`.docx`, `.xlsx`, `.pdf`, `.txt`, `.md`)
- `POST /v1/datasets/{slug}/generate` — kick brief generation (async)
- `DELETE /v1/datasets/{slug}` — remove DB row (files left in place)
- `GET  /v1/brief/current?dataset=…`, `GET /v1/brief/status?dataset=…`, `POST /v1/brief/regenerate?dataset=…`
- `POST /v1/ask` — `{question, dataset}`
- `GET /v1/evidence/{id}`, `POST /v1/evidence` — drill-downs
- `GET /v1/prd/{id}`, `POST /v1/prd` — PRD generation
- `GET /v1/connectors` — list OAuth connections (no tokens in response)
- `GET /v1/connectors/google-drive/authorize?dataset=…` — start Google OAuth (session required)
- `GET /v1/connectors/google-drive/callback` — Google redirect target (configure in GCP)
- `DELETE /v1/connectors/google-drive` — disconnect

The `dataset` query/body parameter is **required** on `/v1/brief/*` and `/v1/ask` — there is no default. The frontend always passes the active slug.

### Google Drive connector env

Set on EC2 `backend/.env` (see `.env.example`): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`, `TOKEN_ENCRYPTION_KEY`, `FRONTEND_URL`. GCP redirect URI must match `GOOGLE_OAUTH_REDIRECT_URI` exactly. File sync from Drive is not implemented yet — OAuth connect/disconnect only.

## Data storage

`DATA_DIR` holds dataset folders (`<slug>/raw/<original-uploads>` + `<slug>/*.md` corpus). In production this points to `/var/lib/sprntly/data` so EC2 `git pull` deploys don't wipe user uploads. `TEMPLATE_DIR` points back into the repo so PRD/evidence templates ship via PRs.

On startup, any on-disk dataset folder (containing at least one root `.md`) is auto-registered in the `datasets` table — covers the pre-existing `asurion` corpus and any folders added manually.

## Tests

`tests/` mirrors `app/`. The `conftest.py` provides:
- `isolated_settings` — fresh DATA_DIR and SQLite under `tmp_path`
- `fake_llm` — patches `app.llm.call_json` so no test ever hits Anthropic
- `app_client` / `unauth_client` — FastAPI `TestClient` with/without a session cookie

Every new feature ships with tests in the same PR; backfill tests for legacy code when you touch it.

## Project layout

```
backend/
├── app/
│   ├── main.py            # FastAPI app + lifespan hooks (seed datasets, init DB)
│   ├── config.py          # env-driven settings (DATA_DIR, TEMPLATE_DIR, DB_PATH)
│   ├── auth.py            # demo-password gate, JWT cookie
│   ├── db.py              # SQLite schema + helpers (briefs, prds, evidences, cached_asks, datasets)
│   ├── corpus.py          # load .md files for a dataset
│   ├── ingest.py          # docx/xlsx/pdf/txt → markdown converters
│   ├── datasets.py        # service layer for create/upload/list
│   ├── brief_runner.py    # background brief gen + drill-down warming
│   ├── ask_runner.py, evidence_runner.py, prd_runner.py
│   └── routes/            # FastAPI routers (datasets, brief, ask, evidence, prd, health)
├── data/                  # PRD/evidence templates (in-repo, TEMPLATE_DIR)
├── tests/                 # pytest suite — runs in CI on every PR
├── deploy/                # nginx, systemd, setup.sh
├── Dockerfile
├── requirements.txt       # runtime
└── requirements-dev.txt   # runtime + pytest
```
