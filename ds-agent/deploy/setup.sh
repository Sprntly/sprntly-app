#!/usr/bin/env bash
set -euo pipefail

# One-shot setup script for the DS-Agent service. Run on the existing
# sprintly-backend EC2 instance as ec2-user. Assumes the monorepo is at
# ~/Sprntly and ~/Sprntly/ds-agent/.env exists (AGENT_PASSWORD and
# ANTHROPIC_API_KEY at minimum).
#
# Run WITHOUT sudo (the script invokes sudo only where required) so
# that $HOME stays pointed at ec2-user, not root.

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this script as the regular ec2-user, not via sudo." >&2
  echo "It calls sudo internally where needed." >&2
  exit 1
fi

AGENT_DIR="$HOME/Sprntly/ds-agent"
cd "$AGENT_DIR"

# Python 3.11 is already installed by the backend setup; we reuse it.
if [ ! -d .venv ]; then
  python3.11 -m venv .venv
fi
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# Persistent upload dir outside the repo so `git reset --hard` deploys
# don't wipe in-flight uploads.
sudo mkdir -p /var/lib/sprntly-agent/uploads
sudo chown -R "$(whoami):$(whoami)" /var/lib/sprntly-agent

# nginx config — the /agent/ location lives in the existing backend nginx
# file; reapply it in case it changed.
sudo cp ~/Sprntly/backend/deploy/nginx.conf /etc/nginx/conf.d/sprintly.conf
sudo nginx -t
sudo systemctl reload nginx

# Systemd unit for the agent.
sudo cp deploy/sprntly-agent.service /etc/systemd/system/sprntly-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now sprntly-agent.service
sudo systemctl restart sprntly-agent.service

echo "---"
echo "Service status:"
sudo systemctl --no-pager status sprntly-agent.service | head -20
echo "---"
echo "Local health check (internal port):"
curl -fsS http://127.0.0.1:8002/health && echo
echo "---"
echo "Public health check (via nginx):"
curl -fsS http://127.0.0.1/agent/health && echo
