# Sprintly Backend

FastAPI backend for [Sprntly](https://www.sprntly.ai) — Cursor for product managers. Deployed to AWS EC2; called by the Vercel-hosted frontend at `https://sprntly.ai`.

## Stack

- Python 3.11 + FastAPI + Uvicorn
- Anthropic Python SDK (Claude API)
- nginx reverse proxy + systemd on Amazon Linux 2023

## Local development

```bash
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

## Deploy to EC2

On a fresh Amazon Linux 2023 t3.micro, as `ec2-user`:

```bash
git clone https://github.com/jainapurva/Sprntly.git
cd Sprntly
cp .env.example .env  # fill in real ANTHROPIC_API_KEY
bash deploy/setup.sh
```

Then add an `A` record `api.sprntly.ai → <elastic-ip>` in the Vercel DNS dashboard (sprntly.ai uses Vercel nameservers), and on the box run:

```bash
sudo dnf -y install certbot python3-certbot-nginx
sudo certbot --nginx -d api.sprntly.ai
```

## Project layout

```
app/
  main.py        # FastAPI app + CORS
  config.py      # env-driven settings
  routes/
    health.py    # /, /healthz
    chat.py      # /v1/chat, /v1/chat/stream
deploy/
  nginx.conf       # reverse proxy on :80
  sprintly.service # systemd unit
  setup.sh         # one-shot provisioning script
Dockerfile
requirements.txt
```
