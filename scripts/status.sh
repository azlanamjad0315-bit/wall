#!/usr/bin/env bash
# status.sh — "kya chal raha hai aur kya toota hai" ek jagah. Kuch nahi badalta.
set -uo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && { set -a; . ./.env; set +a; }
G="http://127.0.0.1:${GATEWAY_PORT:-8787}"; W="http://127.0.0.1:${WAHA_PORT:-3000}"
ok(){ printf '  \033[0;32m✓\033[0m %-22s %s\n' "$1" "$2"; }
no(){ printf '  \033[0;31m✗\033[0m %-22s %s\n' "$1" "$2"; }
mi(){ printf '  \033[1;33m•\033[0m %-22s %s\n' "$1" "$2"; }

echo "── containers ────────────────────────────────────────────"
docker compose ps --format '{{.Service}}|{{.State}}|{{.Status}}' 2>/dev/null | while IFS='|' read -r s st stat; do
  [[ "$st" == "running" ]] && ok "$s" "$stat" || no "$s" "${st:-down} ($stat)"
done || no "docker" "compose nahi chal raha"

echo "── ai-gateway ────────────────────────────────────────────"
if curl -sf "$G/healthz" -o /tmp/gw.json 2>/dev/null; then
  ok "gateway" "$(python3 - <<'PY'
import json
d=json.load(open('/tmp/gw.json'))
print("deploy=%s prompts=%s niches=%s" % (d["deploy_target"], len(d["prompts"]), len(d["niches"])))
PY
)"
  python3 - <<'PY'
import json
d = json.load(open('/tmp/gw.json'))
for p in d.get("providers", []) if isinstance(d.get("providers"), list) else []:
    print("  \033[0;32m✓\033[0m %-22s %s (%s today)" % (p["name"], p["model"], p["today_left"]))
if isinstance(d.get("providers"), str):
    print("  \033[0;31m✗\033[0m %-22s %s" % ("ai provider", d["providers"]))
if d.get("warn"):
    print("  \033[1;33m•\033[0m %-22s %s" % ("warn", d["warn"]))
PY
else
  no "gateway" "khaali jagah — docker compose logs ai-gateway --tail 20"
fi

echo "── whatsapp (WAHA) ───────────────────────────────────────"
S=$(curl -s -H "X-Api-Key: ${WAHA_API_KEY:-}" "$W/api/sessions/${WAHA_SESSION:-default}" 2>/dev/null | \
    python3 -c 'import sys,json
try:
  d=json.load(sys.stdin); st=(d.get("status") or {}).get("state") or d.get("status"); print(st or "?")
except Exception: print("down")' 2>/dev/null || echo down)
case "$S" in
  active) ok "session" "active" ;;
  down)   no "session" "WAHA container hi nahi hai: docker compose up -d waha" ;;
  *)      no "session" "$S → bash scripts/waha-login.sh" ;;
esac

echo "── telegram ──────────────────────────────────────────────"
if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  T=$(curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" | \
      python3 -c 'import sys,json;d=json.load(sys.stdin);print(("ok @"+d["result"]["username"]) if d.get("ok") else "bad token")' 2>/dev/null || echo "network?")
  [[ "$T" == ok* ]] && ok "bot" "$T (chat: ${TELEGRAM_CHAT_ID:-⚠ chat_id unset})" || no "bot" "$T"
  [[ -z "${TELEGRAM_CHAT_ID:-}" ]] && mi "chat_id" "khaali → alerts nahi jayenge"
else
  mi "bot" "TELEGRAM_BOT_TOKEN khaali (alerts optional hain, par recommended)"
fi

echo "── crm ───────────────────────────────────────────────────"
BURL="${BASEROW_URL:-http://baserow:8000}"; [[ "$BURL" == *baserow:8000* ]] && BURL="http://127.0.0.1:${BASEROW_PORT:-8100}"
if [[ -n "${BASEROW_TABLE_ID:-}" && -n "${BASEROW_TOKEN:-}" ]]; then
  R=$(curl -s "$BURL/api/database/rows/table/${BASEROW_TABLE_ID}/?limit=1000" -H "Authorization: Token ${BASEROW_TOKEN}" | \
      python3 -c 'import sys,json
try:
  d=json.load(sys.stdin); rows=d.get("results",[])
  from collections import Counter
  c=Counter(r.get("status") or "-" for r in rows)
  print("ok", len(rows), dict(c))
except Exception: print("bad", 0, {}) ' 2>/dev/null || echo "net 0 {}")
  set -- $R; [[ "$1" == "ok" ]] && ok "baserow" "$2 rows — $3" || no "baserow" "table/token check karein (bash scripts/crm.sh)"
else
  mi "baserow" "BASEROW_TABLE_ID/TOKEN khaali → bash scripts/crm.sh"
fi

echo "── hosting / deploy ─────────────────────────────────────"
case "${DEPLOY_TARGET:-local}" in
  github-pages)
    [[ -n "${GITHUB_TOKEN:-}" ]] || { no "github" "GITHUB_TOKEN khaali"; }
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
      U=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${GITHUB_TOKEN}" https://api.github.com/user)
      [[ "$U" == "200" ]] && ok "github token" "valid" || no "github token" "HTTP $U (fine-grained PAT? Contents:RW?)"
      R=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${GITHUB_TOKEN}" "https://api.github.com/repos/${GITHUB_REPO:-x/y}")
      [[ "$R" == "200" ]] && ok "repo" "$GITHUB_REPO" || no "repo" "$GITHUB_REPO → HTTP $R"
      P=$(curl -s "https://api.github.com/repos/${GITHUB_REPO}/pages" -H "Authorization: Bearer ${GITHUB_TOKEN}" | \
          python3 -c 'import sys,json
d=json.load(sys.stdin); print(d.get("status") or "not enabled", d.get("html_url") or "")' 2>/dev/null || echo "?")
      mi "pages" "$P   (bash scripts/pages-check.sh  for a live probe)"
    fi ;;
  local) mi "local" "DEPLOY_TARGET=local — links gateway /preview/ se aayenge (customers ke liye public nahi)" ;;
  *)   mi "deploy" "DEPLOY_TARGET=${DEPLOY_TARGET}" ;;
esac

echo "── webhook reachability ─────────────────────────────────"
# WAHA ka hook in-network ho sakta hai (http://n8n:5678/...) → host se probe karne ke liye
# usi host ko 127.0.0.1 ke mapped port par try karte hain.
WH_HOOK="$(docker compose exec -T waha printenv WHATSAPP_HOOK_URL 2>/dev/null || true)"
[[ -z "$WH_HOOK" ]] && WH_HOOK="${WAHA_HOOK_URL:-${WEBHOOK_URL:-http://localhost:5678/}}"
case "$WH_HOOK" in
  *n8n:5678*) WH="${WH%/webhook/waha}"; WH="http://127.0.0.1:${N8N_PORT:-5678}/" ;;
  *)          WH="${WH_HOOK%/webhook/waha}/" ;;
esac
if [[ -n "$WH_HOOK" && "$WH_HOOK" != "${WAHA_HOOK_URL:-}" && "$WAHA_HOOK_URL" == http* ]]; then
  no "WAHA hook mismatch" "container: $WH_HOOK  vs  .env: $WAHA_HOOK_URL → docker compose up -d waha"
fi
code=$(curl -s -o /dev/null -w '%{http_code}' "${WH%/}/webhook/waha" -X POST -d '{}' 2>/dev/null || echo 000)
case "$code" in
  200|400|404) ok "n8n webhook" "HTTP $code at ${WH}webhook/waha" ;;
  000)         no "n8n webhook" "${WH}webhook/waha reachable nahi (n8n down?) — public chahiye to: bash scripts/tunnel.sh" ;;
  *)           mi "n8n webhook" "HTTP $code (n8n boot kar raha?) " ;;
esac

echo "── .env sanity (workflows jitne keys maangte hain) ───────"
MISS=0
for k in $(grep -ohE '\$env\.[A-Z0-9_]+' n8n/workflows/*.json | sed 's/\$env\.//' | sort -u); do
  v=$(grep -E "^${k}=" .env 2>/dev/null | head -1 | cut -d= -f2- || true)
  case "$v" in ""|CHANGE_ME*) printf '  \033[1;33m•\033[0m %-26s %s\n' "$k" "${v:-<empty>}"; MISS=$((MISS+1));; esac
done
[[ $MISS == 0 ]] && ok ".env" "sab keys bhare hue hain"
