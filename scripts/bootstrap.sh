#!/usr/bin/env bash
# bootstrap.sh — pehla setup: .env banao, secrets bharo, images uthao, sab up karo.
# Kaise chalayein:  bash scripts/bootstrap.sh
set -uo pipefail
cd "$(dirname "$0")/.."
GREEN='\033[0;32m'; Y='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say()  { printf "${GREEN}▸${NC} %s\n" "$*"; }
warn() { printf "${Y}!${NC} %s\n" "$*"; }
die()  { printf "${RED}✗${NC} %s\n" "$*"; exit 1; }

command -v docker >/dev/null || die "Docker nahi mila. https://docs.docker.com/engine/install/ (Linux) ya Docker Desktop (Mac/Win)"
docker compose version >/dev/null 2>&1 || die "docker compose plugin nahi mila (docker-compose v2 chahiye)"

PROFILE_ARGS=()
for p in "$@"; do
  case "$p" in
    --with-baserow)  PROFILE_ARGS+=(--profile baserow) ;;
    --with-ollama)   PROFILE_ARGS+=(--profile local-ai) ;;
    --with-tunnel)   PROFILE_ARGS+=(--profile tunnel) ;;
    --no-build)      NOBUILD=1 ;;
    *) warn "unknown flag: $p" ;;
  esac
done

say "data dirs (permission-safe)"
bash scripts/dirs.sh >/dev/null

# ---------------------------------------------------------------- 1. .env ----
if [[ ! -f .env ]]; then
  cp .env.example .env
  say ".env bana diya (.env.example se copy)"
else
  say ".env pehle se maujood — overwrite nahi kiya"
fi

rnd() { (openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n') ; }
setkey() {  # setkey KEY -> .env me KEY=<random> (agar CHANGE_ME/empty ho)
  local k="$1" v cur
  v="$(rnd)"
  cur="$(grep -E "^${k}=" .env | head -1 | cut -d= -f2- || true)"
  if [[ -z "$cur" || "$cur" == CHANGE_ME* ]]; then
    if grep -qE "^${k}=" .env; then
      sed -i.bak -E "s|^${k}=.*|${k}=${v}|" .env && rm -f .env.bak
    else
      printf '%s=%s\n' "$k" "$v" >> .env
    fi
    say "${k} generate kar diya"
  fi
}
setkey N8N_ENCRYPTION_KEY
setkey GATEWAY_TOKEN
setkey WAHA_API_KEY
setkey WAHA_DASHBOARD_PASS

# ------------------------------------------------- 2. still-missing check ----
need=()
grep -qE '^GEMINI_API_KEY=.+'  .env || grep -qE '^GROQ_API_KEY=.+'   .env || need+=("GEMINI_API_KEY (ya GROQ_API_KEY)")
grep -qE '^CONTACT_EMAIL=[^y]' .env || need+=("CONTACT_EMAIL (Nominatim policy ke liye)")
grep -qE '^AGENCY_NAME=.+'     .env || true
if [[ ${#need[@]} -gt 0 ]]; then
  warn ".env me yeh abhi bhi khaali hain (baad me bhi bhar sakte hain):"
  for n in "${need[@]}"; do printf '     • %s\n' "$n"; done
fi

# --------------------------------------------------------------- 3. build ----
if [[ "${NOBUILD:-0}" != "1" ]]; then
  say "gateway image build ho rahi (1-3 min)…"
  docker compose "${PROFILE_ARGS[@]}" build gateway ai-gateway 2>/dev/null || docker compose "${PROFILE_ARGS[@]}" build
fi
say "services up…"
docker compose "${PROFILE_ARGS[@]}" up -d
sleep 3
docker compose "${PROFILE_ARGS[@]}" ps

say "gateway ka health check…"
for i in $(seq 1 20); do
  if curl -sf http://127.0.0.1:8787/healthz >/dev/null 2>&1; then
    curl -s http://127.0.0.1:8787/healthz | head -c 600; echo; break
  fi
  [[ $i == 20 ]] && warn "gateway 20 baar me healthz pe nahi aaya — docker compose logs ai-gateway --tail 40"
  sleep 2
done

cat <<'EOT'

────────────────────────────────────────────────────────────────────────
 Ab aage (order mat badlein):
   1) bash scripts/crm.sh                 # Baserow table + fields (2 min)
   2) bash scripts/waha-login.sh          # WhatsApp QR scan (1 min)
   3) bash scripts/tunnel.sh              # free public webhook URL (30 sec)
   4) n8n kholen http://localhost:5678 → 4 workflows import (README step 5)
   5) bash scripts/dryrun.sh              # bina message bheje poora test
   6) workflows 01→04 activate + schedules on
   7) subah uth kar Telegram check karein 🙂
────────────────────────────────────────────────────────────────────────
EOT
