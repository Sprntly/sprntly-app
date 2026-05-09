#!/usr/bin/env bash
set -euo pipefail

# One-shot setup script. Run on a fresh Amazon Linux 2023 t3.micro as ec2-user.
# Assumes the monorepo is cloned at ~/Sprntly and ~/Sprntly/backend/.env exists.

BACKEND_DIR="$HOME/Sprntly/backend"
cd "$BACKEND_DIR"

sudo dnf -y update
sudo dnf -y install python3.11 python3.11-pip nginx git

if [ ! -d .venv ]; then
  python3.11 -m venv .venv
fi
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

sudo cp deploy/nginx.conf /etc/nginx/conf.d/sprintly.conf
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx

sudo cp deploy/sprintly.service /etc/systemd/system/sprintly.service
sudo systemctl daemon-reload
sudo systemctl enable --now sprintly.service
sudo systemctl restart sprintly.service

echo "---"
echo "Service status:"
sudo systemctl --no-pager status sprintly.service | head -20
echo "---"
echo "Local health check:"
curl -fsS http://127.0.0.1:8000/healthz && echo
