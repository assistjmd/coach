#!/usr/bin/env bash
# Initial provisioning script for the coach EC2 box. Run as `ubuntu` user.
# Assumes: Ubuntu 22.04, Elastic IP attached, SSH access via Jake's key.
set -euo pipefail

sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip postgresql-16 nginx git build-essential libpq-dev

# Postgres
sudo -u postgres psql -c "CREATE USER coach WITH PASSWORD 'CHANGE_ME';" || true
sudo -u postgres psql -c "CREATE DATABASE coach OWNER coach;" || true

# Code
if [ ! -d /home/ubuntu/coach ]; then
  git clone https://github.com/assistjmd/coach.git /home/ubuntu/coach
fi
cd /home/ubuntu/coach/backend
python3.11 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .

# Env file
sudo mkdir -p /etc/coach
sudo cp .env.example /etc/coach/env
sudo chmod 600 /etc/coach/env
echo "EDIT /etc/coach/env with real secrets before starting the service"

# Systemd + nginx
sudo cp deploy/coach.service /etc/systemd/system/coach.service
sudo systemctl daemon-reload
sudo systemctl enable coach
sudo cp deploy/nginx.conf /etc/nginx/sites-available/coach
sudo ln -sf /etc/nginx/sites-available/coach /etc/nginx/sites-enabled/coach
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

echo "Next steps:"
echo "  1) Edit /etc/coach/env"
echo "  2) Run: cd /home/ubuntu/coach/backend && .venv/bin/alembic upgrade head"
echo "  3) sudo systemctl start coach"
echo "  4) Add nightly cron: 0 3 * * * /home/ubuntu/coach/backend/deploy/backup.sh"
