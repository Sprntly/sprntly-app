# Sprntly DS Agent

A data-science chat agent: a product manager loads a CSV and chats with Claude. Claude has a Python sandbox (Anthropic's server-side `code_execution` tool) and writes its own analyses — pandas, sklearn, scipy, statsmodels, matplotlib, shap pre-installed — instead of running a fixed pipeline.

The original spec is in the [internal Drive folder](https://drive.google.com/drive/u/0/folders/1VsvqdKwVqrjp6w4kzMyF5BzlHRn6QzSP). The first attempt — a fixed PCA + SHAP + stratified pipeline wrapped as tools the chat agent could call — is preserved under `ds_agent/legacy/` for reference. It was replaced because tool-wrapping a fixed pipeline meant the agent could only find patterns we'd coded for. The current design gives the agent code-execution and lets it decide what to compute.

## Layout

```
ds-agent/
├── ds_agent/
│   ├── synthetic.py          # synthetic SaaS dataset (ground-truth effects planted)
│   ├── cli.py                # `ds-agent gen-synthetic`
│   ├── server/               # FastAPI chat service at /agent
│   │   ├── app.py            # routes
│   │   ├── auth.py           # bearer-token sessions
│   │   ├── chat.py           # Claude loop with server-side code_execution
│   │   ├── tools.py          # Files API uploader (CSV → Anthropic)
│   │   ├── state.py          # in-process per-session store
│   │   ├── static/           # vanilla HTML/CSS/JS UI
│   │   └── samples/          # bundled sample CSVs
│   └── legacy/               # archived first attempt — fixed PCA + SHAP pipeline,
│                             # confidence scoring, narrative writer. Kept for
│                             # comparison / partial reuse; not imported by the
│                             # running service.
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

- Streaming `/api/chat` responses — the UI shows "Agent is thinking…" and the connection can take a while on heavy turns. **Note:** Vercel terminates rewrites at ~150s, so longer analyses fail through `sprntly.ai/agent`. Hit `api.sprntly.ai/agent` directly (no Vercel proxy) for slower work; `proxy_read_timeout` on nginx is 600s.
- Per-user usage tracking (single shared password for v1).
- Connectors for Amplitude / Mixpanel / GA4 / Pendo / PostHog — only CSV upload today.
