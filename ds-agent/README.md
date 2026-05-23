# Sprntly DS Agent

A data-science chat agent that runs against analytics CSVs and surfaces ranked behavioral drivers, confidence-scored.

The pipeline is described in detail in the [internal spec](https://drive.google.com/drive/u/0/folders/1VsvqdKwVqrjp6w4kzMyF5BzlHRn6QzSP); this is the first slice — Stage 1 (Pattern Discovery) wired into a chat UI.

## Layout

```
ds-agent/
├── ds_agent/                 # core pipeline + chat web service
│   ├── ingest.py             # CSV intake + data-quality assessment
│   ├── synthetic.py          # synthetic SaaS dataset (ground-truth effects planted)
│   ├── stages/
│   │   └── pattern_discovery.py   # Stage 1: PCA + SHAP + Stratified
│   ├── confidence.py         # 5-factor HIGH/MEDIUM/LOW scoring
│   ├── synthesis.py          # one-shot narrative writer (used by tools)
│   ├── pipeline.py           # orchestrator + cross-method consolidation
│   ├── output.py             # spec §3.3 JSON shape
│   ├── cli.py                # `ds-agent gen-synthetic` / `ds-agent run`
│   └── server/               # FastAPI chat service at /agent
│       ├── app.py            # routes
│       ├── auth.py           # shared-password + signed-cookie sessions
│       ├── chat.py           # tool-using Claude loop
│       ├── tools.py          # ds-agent tools exposed to the LLM
│       ├── state.py          # in-process per-session store
│       ├── static/           # vanilla HTML/CSS/JS UI
│       └── samples/          # bundled sample CSVs
├── deploy/
│   ├── sprntly-agent.service # systemd unit
│   └── setup.sh              # one-shot installer for EC2
└── tests/
```

## Local development

```bash
cd ds-agent
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest

# Boot the chat service against the bundled sample dataset
export AGENT_PASSWORD=dev
export AGENT_COOKIE_SECURE=0
export ANTHROPIC_API_KEY=sk-ant-...     # required for /api/chat
.venv/bin/uvicorn ds_agent.server.__main__:app --reload --port 8002

# Then visit http://127.0.0.1:8002/
```

## Production

Lives on the existing `sprintly-backend` EC2 host as a separate systemd unit (`sprntly-agent.service`) on port 8002. Nginx (`backend/deploy/nginx.conf`) proxies `api.sprntly.ai/agent/*` to it. The marketing site rewrites `sprntly.ai/agent/*` → `api.sprntly.ai/agent/*` (see `davidkmumuni-lab/sprntlyai_website` `vercel.json`).

First-time setup on a fresh box:

```bash
# ssh ec2-user@<instance>
cd ~/Sprntly/ds-agent
# Create .env with AGENT_PASSWORD and ANTHROPIC_API_KEY
sudo bash deploy/setup.sh
```

Subsequent deploys land via the `Deploy ds-agent to EC2` GitHub Actions workflow on every push to `main` that touches `ds-agent/**`.

## Required env

| var                  | who needs it                                |
| -------------------- | ------------------------------------------- |
| `AGENT_PASSWORD`     | server — required, used to gate access      |
| `ANTHROPIC_API_KEY`  | server — required for `/api/chat`           |
| `AGENT_COOKIE_SECRET`| server — optional; if unset, sessions reset on restart |
| `AGENT_COOKIE_SECURE`| server — optional; defaults to "1" (HTTPS only) |
| `AGENT_UPLOAD_DIR`   | server — optional; defaults to `/tmp/...`   |
| `AGENT_MODEL`        | server — optional; defaults to claude-sonnet-4-6 |

## What's not done yet

- Stages 2–5 of the spec (Temporal, Tail, Causal, Interactions) — currently null in the output JSON.
- Streaming responses on `/api/chat` (the UI shows a "Agent is thinking…" status instead).
- Per-user usage tracking (single shared password for v1).
- Connectors for Amplitude / Mixpanel / GA4 / Pendo / PostHog — only CSV ingest today.
