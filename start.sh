#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example"
  echo "Edit USER_LAT and USER_LON in .env if you want to use your own location."
fi

mkdir -p logs

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed or not available in PATH."
  echo "Install Docker, then run ./start.sh again."
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  echo "Docker Compose is not available."
  echo "Install Docker Compose, then run ./start.sh again."
  exit 1
fi

echo "Starting Aircraft Transit Hunter..."
echo "Mode is controlled by RUN_MODE in .env. Default: quiet."
echo "Logs: logs/aircraft-transit-$(date +%F).log"
echo

"${COMPOSE_CMD[@]}" up --build
