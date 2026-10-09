#!/usr/bin/env bash
# tunnel.sh — free public HTTPS URL for the WAHA webhook (koi domain/account nahi chahiye).
# Cloudflare quick tunnel: URL har restart pe badalta hai. Stable chahiye to
# docs/tunnel.md me named-tunnel (free, apne domain ke saath) ka tareeqa hai.
set -uo pipefail
cd "$(dirname "$0")/.."
GREEN='\033[0;32m'; Y='\033[1;33m'; NC='\033[0m'
say(){ printf "${GREEN}▸${NC} %s\n" "$*"; }; warn(){ printf "${Y}!${NC} %s\n" "$*"; }

say "cloudflared (quick tunnel) start…"
docker compose --profile tunnel up -d tunnel >/dev/null 2>&1 || {
  warn "compose me tunnel profile nahi utha — direct try:"; docker run -d --name demosites_tunnel --restart unless-stopped \
    --network "$(docker network ls -qf name=demosites-ai_default | head -1)" \
    cloudflare/cloudflared:latest tunnel --no-autoupdate --url http://n8n:5678 >/dev/null; }

URL=""
for i in $(seq 1 25); do
  URL=$(docker compose logs tunnel 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
  [[ -z "$URL" ]] && URL=$(docker logs demosites_tunnel 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1)
  [[ -n "$URL" ]] && break
  sleep 2
done
[[ -n "$URL" ]] || { warn "URL nahi mila — docker compose logs tunnel dekh lein"; exit 1; }
say "public URL: $URL"

for k in WEBHOOK_URL N8N_EDITOR_BASE_URL WAHA_HOOK_URL; do
  v="$URL/"
  [[ "$k" == "WAHA_HOOK_URL" ]] && v="$URL/webhook/waha"
  if grep -qE "^${k}=" .env; then sed -i.bak -E "s|^${k}=.*|${k}=${v}|" .env && rm -f .env.bak
  else printf '%s=%s\n' "$k" "$v" >> .env; fi
done
say ".env me WEBHOOK_URL + N8N_EDITOR_BASE_URL + WAHA_HOOK_URL update"
docker compose up -d n8n waha >/dev/null 2>&1
sleep 6
if curl -sf "${URL}/healthz" >/dev/null 2>&1; then say "webhook rasta live: ${URL}/webhook/waha (n8n 03 ko WAHA yahi bhejega)"; else
  warn "${URL}/healthz turant nahi aaya — n8n abhi boot kar raha hoga, 20 sec baad dobara: curl -I ${URL}/healthz"; fi
echo
say "WAHA ko webhook dobara set karna hai (agar session pehle se active tha):"
echo "    docker compose restart waha"
echo "    # WHATSAPP_HOOK_URL already ${URL}/webhook/waha  → compose me set hai"
echo
warn "NOTE: quick tunnel ka URL badalta hai jab container restart hota hai → phir yeh script dobara chalayein."
