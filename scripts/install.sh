#!/usr/bin/env bash
set -euo pipefail

PORT="${DIVOOM_PORT:-8080}"
LAYOUT="${DIVOOM_LAYOUT:-config/layouts/dashboard.json}"
INSTALL_SERVICE="${INSTALL_SERVICE:-0}"
PYTHON_BIN="${PYTHON:-python3}"

cd "$(dirname "$0")/.."

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.10+ is required." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi

. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

mkdir -p config/layouts
if [ ! -f config/datasources.json ] && [ -f config/datasources.example.json ]; then
  cp config/datasources.example.json config/datasources.json
fi
if [ ! -f config/device.json ] && [ -f config/device.example.json ]; then
  cp config/device.example.json config/device.json
fi

if [ "$INSTALL_SERVICE" = "1" ]; then
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found; cannot install service." >&2
    exit 1
  fi
  sudo cp divoom@.service /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now "divoom@$USER"
  echo "Service installed. Open http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PORT}"
else
  echo "Installed. Start Studio with:"
  echo "  . .venv/bin/activate && divoom serve ${LAYOUT} --web --port ${PORT} --no-device"
fi
