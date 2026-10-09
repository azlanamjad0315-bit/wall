#!/usr/bin/env bash
# dryrun.sh — poora pipeline TEST, bina kisi customer ko message bheje.
#   bash scripts/dryrun.sh                       # 3 leads, 1 demo, 1 message (print only)
#   bash scripts/dryrun.sh --area "Gulberg, Lahore" --niche restaurant
#   bash scripts/dryrun.sh --to 923001234567     # generated message APNE number pe bhejo (real test)
set -uo pipefail
cd "$(dirname "$0")/.."
G="http://127.0.0.1:${GATEWAY_PORT:-8787}"
[[ -f .env ]] && { set -a; . ./.env; set +a; }
H=(-H "Content-Type: application/json")
[[ -n "${GATEWAY_TOKEN:-}" ]] && H+=(-H "X-Gateway-Token: ${GATEWAY_TOKEN}")
AREA="${AREA:-DHA Phase 5, Karachi}"; NICHE=""; LIMIT=3; SENDTO=""; SKIPNET=0
while [[ $# -gt 0 ]]; do case "$1" in
  --area) AREA="$2"; shift 2;; --niche) NICHE="$2"; shift 2;; --limit) LIMIT="$2"; shift 2;;
  --to) SENDTO="$2"; shift 2;; --offline) SKIPNET=1; shift;;
  *) echo "unknown: $1"; exit 1;; esac; done
J() { python3 -c "import sys,json
d=json.load(sys.stdin)
$1" 2>/dev/null; }
step() { printf '\n\033[1;32m%s\033[0m\n' "$*"; }

step "1) gateway health"
curl -sf "${G}/healthz" | python3 -m json.tool || { echo "❌ gateway nahi chal raha: docker compose up -d"; exit 1; }

if [[ $SKIPNET == 1 ]]; then
  step "offline mode — network calls skip (baseline checks done). ✅"; exit 0
fi

step "2) lead scan (OpenStreetMap → sirf jin ki website nahi) — area: $AREA"
SCAN=$(curl -s -X POST "${H[@]}" "$G/v1/leads/scan" -d "$(python3 -c '
import json,sys
b={"areas":sys.argv[1],"limit":int(sys.argv[2]),"min_hotness":0,"require_contact":"true"}
a=sys.argv[3]
if a: b["niches"]=[a]
print(json.dumps(b))' "$AREA" "$LIMIT" "$NICHE")")
echo "$SCAN" | J 'print("stats:",json.dumps(d.get("stats",{})))
for l in d.get("leads",[])[:5]:
    print(" • %-28s %-16s hot=%-3s ph=%s site=%s" % (l["osm_name"][:28], l["category_label"][:16], l["hotness"], l.get("phone","")[-7:], l.get("website") or "NONE"))'
LEAD=$(echo "$SCAN" | python3 -c 'import sys,json
try: print(json.dumps(json.load(sys.stdin)["leads"][0]))
except Exception: print("")')
[[ -n "$LEAD" ]] || { echo "❌ is area me koi lead nahi mila (OSM data kam ho sakta hai) — doosra area try karein: --area \"Clifton Block 5, Karachi\""; exit 1; }

step "3) AI demo site generate (Gemini→Groq→Ollama, phir HTML gate + 1 repair)"
GEN=$(curl -s -X POST "${H[@]}" "$G/v1/demo/generate" -d "$(python3 -c 'import sys,json
l=json.loads(sys.argv[1])
keep=["osm_name","category","category_label","address","city","area","country","phone","whatsapp","rating","reviews_count","opening_hours","notes","slug","language"]
print(json.dumps({k:l.get(k,"") for k in keep}))' "$LEAD")")
echo "$GEN" | J 'print(" ok        :", d.get("ok"))
print(" slug      :", d.get("slug"))
print(" bytes     :", d.get("bytes"))
print(" design    :", json.dumps(d.get("design",{})))
print(" provider  :", d.get("provider"), "/", d.get("model"), "| quota left:", d.get("quota_left"))
print(" attempts  :", d.get("attempts"))
if d.get("issues"): print(" issues    :", d["issues"][:4])'
HTML=$(echo "$GEN" | python3 -c 'import sys,json
print(json.load(sys.stdin).get("html",""))' 2>/dev/null)
[[ ${#HTML} -gt 3000 ]] || { echo "❌ HTML chhota aa raha (AI key check karein)"; exit 1; }
echo "$HTML" > /tmp/dryrun-demo.html
echo "   preview file: /tmp/dryrun-demo.html  ($(wc -c </tmp/dryrun-demo.html) bytes) — browser me khol lein"

step "4) deploy (DEPLOY_TARGET=${DEPLOY_TARGET:-github-pages})"
OUT=$(curl -s -X POST "${H[@]}" "$G/v1/site/deploy" -d "$(python3 -c 'import sys,json
g=json.loads(sys.argv[1])
print(json.dumps({"slug":g.get("slug"),"html":g.get("html"),"title":g.get("title"),"meta_description":g.get("meta_description"),"business_name":g.get("lead",{}).get("osm_name",""),"force":"true"}))' "$GEN")")
echo "$OUT" | python3 -m json.tool 2>/dev/null | head -20
URL=$(echo "$OUT" | J 'print(d.get("url",""))')

step "5) outreach message (Roman Urdu / English auto) — PRINT ONLY, koi send nahi hua"
MSG=$(curl -s -X POST "${H[@]}" "$G/v1/outreach" -d "$(python3 -c 'import sys,json
l=json.loads(sys.argv[1]); u=sys.argv[2]
print(json.dumps({**{k:l.get(k,"") for k in ["osm_name","category_label","city","area","country","phone","whatsapp","rating","reviews_count","language","notes"]}, "business_name":l["osm_name"], "demo_url":u}))' "$LEAD" "$URL")")
echo "$MSG" | python3 -c 'import sys,json
d=json.load(sys.stdin)
print("words   :", d.get("words"), "| language:", d.get("language"), "| channel target:", d.get("chat_id"))
print("subject :", d.get("subject"))
print("─────── MESSAGE ───────")
print(d.get("message"))
print("───────────────────────")'

if [[ -n "$SENDTO" ]]; then
  step "6) --to $SENDTO : AAPKE number pe bhej rahe hain (real WhatsApp test)"
  CID="$(echo "$SENDTO" | tr -cd '0-9')@c.us"
  TEXT=$(echo "$MSG" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("message",""))' | sed 's/"/\\"/g' | tr '\n' ' ')
  curl -s -X POST "http://127.0.0.1:${WAHA_PORT:-3000}/api/sendText" -H "Content-Type: application/json" \
    -H "X-Api-Key: ${WAHA_API_KEY}" \
    -d "{\"session\":\"${WAHA_SESSION:-default}\",\"chatId\":\"$CID\",\"text\":\"$TEXT\"}" | head -c 300
  echo
  say "bhej diya — phone pe check karein (link khulna chahiye, mobile pe sahi dikhna chahiye)"
fi

printf '\n\033[1;33m✅ dry-run khatam.\033[0m Koi customer ko message nahi gaya (jab tak --to na dein).\n'
printf '   Test checklist:\n'
printf '     • /tmp/dryrun-demo.html browser me khola? mobile width (390px) pe theek?\n'
printf '     • WhatsApp button → aapke business ke number pe khulta hai?\n'
printf '     • Map embed me sahi location?\n'
printf '     • Neeche footer me aapka DEMO strip + WhatsApp link?\n'
printf '     • Message me price nahi aaya? (pehli message me price = 🚫)\n'
