#!/usr/bin/env bash
set -euo pipefail
cd /opt/openslt
python3.8 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install .
.venv/bin/alembic upgrade head
npm --prefix frontend ci
npm --prefix frontend run build
install -m 0644 deploy/systemd/openslt-*.service /etc/systemd/system/
install -m 0644 deploy/nginx/openslt.conf /etc/nginx/conf.d/openslt.conf
systemctl daemon-reload
systemctl enable --now openslt-api openslt-worker openslt-beat
nginx -t && systemctl reload nginx
