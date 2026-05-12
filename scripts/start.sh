#!/usr/bin/env bash
# Bring the bot stack up. Idempotent — safe to re-run.
# Called by systemd at boot; can also be invoked manually.
set -euo pipefail

# cd to the project root regardless of where the script is called from.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."

exec docker compose up -d --remove-orphans
