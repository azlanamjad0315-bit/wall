#!/usr/bin/env bash
# crm.sh — Baserow me 'leads' table + 44 fields, aur .env me BASEROW_TABLE_ID.
#   bash scripts/crm.sh                      # token .env se legi
#   bash scripts/crm.sh --token XXXX           # explicit
#   bash scripts/crm.sh --reset                # saare rows clear (fresh start)
#   bash scripts/crm.sh --url http://... --token ...   # hosted Baserow pe bhi chalta hai
set -uo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { cp .env.example .env; }
# shellcheck disable=SC1090
set -a; . ./.env; set +a
command -v python3 >/dev/null || { echo "python3 chahiye (script chalane ke liye)"; exit 1; }

URL="${BASEROW_URL:-http://baserow:8000}"
# container ke andar Baserow 'baserow:8000' pe hai; host se chalate waqt localhost:8100
if echo "$URL" | grep -q "baserow:8000"; then URL="http://127.0.0.1:${BASEROW_PORT:-8100}"; fi

docker compose ps baserow 2>/dev/null | grep -q "baserow" || \
  { echo "! Baserow container nahi chal raha — on karein:"; \
    echo "    docker compose --profile baserow up -d   (phir ~40 sec rukein)"; }

exec python3 tools/baserow_setup.py --url "$URL" --token "${BASEROW_TOKEN:-}" "$@"
