#!/usr/bin/env bash
# waha-login.sh — WhatsApp session banata hai aur QR dikhata hai (scan = 1 min).
# ⚠️ Apna PRIMARY number mat dein: dedicated/spare number use karein (docs/anti-ban.md).
set -uo pipefail
cd "$(dirname "$0")/.."
GREEN='\033[0;32m'; Y='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
say(){ printf "${GREEN}▸${NC} %s\n" "$*"; }; warn(){ printf "${Y}!${NC} %s\n" "$*"; }; die(){ printf "${RED}✗${NC} %s\n" "$*"; exit 1; }

[[ -f .env ]] || die ".env nahi mila — pehle: bash scripts/bootstrap.sh"
# shellcheck disable=SC1090
set -a; . ./.env; set +a
KEY="${WAHA_API_KEY:-}"; BASE="http://127.0.0.1:${WAHA_PORT:-3000}"; SESS="${WAHA_SESSION:-default}"
H=(-H "Content-Type: application/json")
[[ -n "$KEY" ]] && H+=(-H "X-Api-Key: $KEY")

command -v curl >/dev/null || die "curl nahi mila"
curl -sf "${H[@]}" "$BASE/api/swagger.json" >/dev/null 2>&1 || die "WAHA $BASE pe nahi chal raha. docker compose ps / logs waha"

state=$(curl -s "${H[@]}" "$BASE/api/sessions/$SESS" | python3 -c 'import sys,json;
try:
  d=json.load(sys.stdin); print((d.get("status") or {}).get("state") or d.get("status") or "none")
except Exception: print("missing")' 2>/dev/null || echo missing)
say "session '$SESS' state: $state"

if [[ "$state" == "missing" ]]; then
  say "session bana rahe…"
  curl -s -X POST "${H[@]}" "$BASE/api/sessions" -d "{\"name\":\"$SESS\"}" >/dev/null
  state=STOPPED
fi
if [[ "$state" != "active" ]]; then
  say "session start…"
  curl -s -X POST "${H[@]}" "$BASE/api/sessions/$SESS/start" >/dev/null
fi

QR=$(mktemp -t waha-qr-XXXX.png)
for i in $(seq 1 30); do
  for ep in "/api/$SESS/qr?download=false" "/api/$SESS/auth/qr?download=false"; do
    raw=$(curl -s "${H[@]}" "$BASE$ep")
    b64=$(printf '%s' "$raw" | python3 -c 'import sys,json
try:
  d=json.load(sys.stdin)
  print((d.get("data") or "").split(",")[-1])
except Exception: print("")' 2>/dev/null)
    if [[ -n "${b64:-}" && ${#b64} -gt 200 ]]; then
      printf '%s' "$b64" | base64 -d > "$QR" 2>/dev/null || true
      if [[ -s "$QR" ]]; then
        cp "$QR" waha-qr.png 2>/dev/null
        say "QR tayyar ✅  — ise phone se scan karein:"
        echo
        echo "     xdg-open waha-qr.png      # Linux"
        echo "     open waha-qr.png          # Mac"
        echo "   ya browser me: $BASE/dashboard  → session → QR"
        echo
        warn "QR 30 second me expire hota hai — na chale to dobara chalayein: bash scripts/waha-login.sh"
        break 2
      fi
    fi
  done
  sleep 2
done

say "login confirm ho raha (max 90s)…"
for i in $(seq 1 30); do
  s=$(curl -s "${H[@]}" "$BASE/api/sessions/$SESS" | python3 -c 'import sys,json
try: print((json.load(sys.stdin).get("status") or {}).get("state","?"))
except Exception: print("?")' 2>/dev/null)
  [[ "$s" == "active" ]] && { say "✅ WhatsApp connected (state=active)"; exit 0; }
  sleep 3
done
warn "abhi bhi active nahi ($s). Phone me: WhatsApp → Settings → Linked Devices → Link a Device"
warn "logs: docker compose logs waha --tail 40"
exit 1
