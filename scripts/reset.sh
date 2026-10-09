#!/usr/bin/env bash
# reset.sh — CRM ke saare rows delete (fresh start). Table/fields nahi jayenge.
#   bash scripts/reset.sh            # confirm maangega
#   bash scripts/reset.sh --force
set -uo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && { set -a; . ./.env; set +a; }
[[ "${1:-}" != "--force" ]] && { read -r -p "$(printf '\033[1;33m⚠️  saare leads/rows delete ho jayenge. continue? (yes/N) \033[0m')" a; [[ "${a,,}" == "yes" ]] || { echo "cancel"; exit 1; }; }
BURL="${BASEROW_URL:-http://baserow:8000}"; [[ "$BURL" == *baserow:8000* ]] && BURL="http://127.0.0.1:${BASEROW_PORT:-8100}"
exec python3 tools/baserow_setup.py --url "$BURL" --token "${BASEROW_TOKEN:-}" \
     --env .env --reset
