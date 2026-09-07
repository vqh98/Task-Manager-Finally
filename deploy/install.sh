#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root."
  exit 1
fi

APP_ROOT=/opt/task-adviser
APP_DIR="$APP_ROOT/app"
DATA_DIR="$APP_ROOT/data"
SERVICE_USER=taskadviser

id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$APP_ROOT" --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$APP_DIR" "$DATA_DIR"

apt-get update
apt-get install -y python3 python3-venv ca-certificates
python3 -m venv "$APP_ROOT/venv"
"$APP_ROOT/venv/bin/pip" install --upgrade pip
"$APP_ROOT/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

chown -R root:root "$APP_DIR" "$APP_ROOT/venv"
chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"
if [[ -f "$DATA_DIR/.env" ]]; then chmod 600 "$DATA_DIR/.env"; fi
if [[ -f "$DATA_DIR/credentials.json" ]]; then chmod 600 "$DATA_DIR/credentials.json"; fi
if [[ -f "$DATA_DIR/token.json" ]]; then chmod 600 "$DATA_DIR/token.json"; fi

cp "$APP_DIR/deploy/task-adviser.service" /etc/systemd/system/task-adviser.service
systemctl daemon-reload
systemctl enable task-adviser.service

if [[ ! -f "$DATA_DIR/.env" || ! -f "$DATA_DIR/credentials.json" ]]; then
  echo "Required secrets are missing in $DATA_DIR (.env and credentials.json). Service was installed but not started."
  exit 0
fi

systemctl restart task-adviser.service
systemctl --no-pager --full status task-adviser.service
