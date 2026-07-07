#!/usr/bin/env bash
set -euo pipefail

# One-shot setup script for the MCP service. Run on the existing
# sprintly-backend EC2 instance as ec2-user. Assumes the monorepo is at
# ~/Sprntly and ~/Sprntly/mcp/.env exists (BACKEND_URL and
# BACKEND_INTERNAL_KEY at minimum).
#
# Run WITHOUT sudo (the script invokes sudo only where required) so
# that $HOME stays pointed at ec2-user, not root.

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this script as the regular ec2-user, not via sudo." >&2
  echo "It calls sudo internally where needed." >&2
  exit 1
fi

MCP_DIR="$HOME/Sprntly/mcp"
cd "$MCP_DIR"

# Python 3.11 is already installed by the backend setup; we reuse it.
if [ ! -d .venv ]; then
  python3.11 -m venv .venv
fi
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# nginx config — the /mcp location lives in the existing backend nginx
# file; reapply it in case it changed.
sudo cp ~/Sprntly/backend/deploy/nginx.conf /etc/nginx/conf.d/sprintly.conf
sudo nginx -t
sudo systemctl reload nginx

# Systemd unit for the MCP server.
sudo cp deploy/sprntly-mcp.service /etc/systemd/system/sprntly-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now sprntly-mcp.service
sudo systemctl restart sprntly-mcp.service

echo "---"
echo "Service status:"
sudo systemctl --no-pager status sprntly-mcp.service | head -20
echo "---"
echo "Local health check (internal port):"
curl -fsS http://127.0.0.1:8003/health && echo
