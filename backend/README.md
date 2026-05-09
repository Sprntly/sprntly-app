# Sprntly Backend

FastAPI backend for [Sprntly](https://www.sprntly.ai). Deployed to AWS EC2 at `api.sprntly.ai`. Called by the Next.js demo at `sprntly.ai/demo`.

## Stack

- Python 3.11 + FastAPI + Uvicorn
- Anthropic Python SDK (Claude API)
- nginx reverse proxy + systemd on Amazon Linux 2023

## Local development

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```

Then `curl http://127.0.0.1:8000/healthz`.

## Endpoints

- `GET /` — service info
- `GET /healthz` — liveness probe
- `POST /v1/chat` — non-streaming Claude completion
- `POST /v1/chat/stream` — SSE streaming Claude completion

## Project layout

```
backend/
├── app/
│   ├── main.py       # FastAPI app + CORS
│   ├── config.py     # env-driven settings
│   └── routes/
│       ├── health.py # /, /healthz
│       └── chat.py   # /v1/chat, /v1/chat/stream
├── deploy/
│   ├── nginx.conf       # reverse proxy on :80
│   ├── sprintly.service # systemd unit
│   └── setup.sh         # one-shot provisioning script
├── Dockerfile
└── requirements.txt
```
