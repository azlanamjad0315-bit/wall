#!/usr/bin/env bash
# voice-note.sh — ek AI voice note bana kar kisi number pe bhejein (calling ka FREE substitute).
#   bash scripts/voice-note.sh 923001234567 "Bismillah Bakers" "https://u.github.io/demo-sites/bismillah/"
#   bash scripts/voice-note.sh --text-only 923001234567 "Bismillah Bakers"
set -uo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && { set -a; . ./.env; set +a; }
G="http://127.0.0.1:${GATEWAY_PORT:-8787}"; T="http://127.0.0.1:${TTS_PORT:-8090}"
H=(-H "Content-Type: application/json"); [[ -n "${GATEWAY_TOKEN:-}" ]] && H+=(-H "X-Gateway-Token: ${GATEWAY_TOKEN}")
TEXTONLY=0; [[ "${1:-}" == "--text-only" ]] && { TEXTONLY=1; shift; }
PHONE="${1:-}"; BIZ="${2:-Bismillah Bakers}"; URL="${3:-}"
[[ -n "$PHONE" ]] || { echo "use: bash scripts/voice-note.sh <number> \"<business>\" [\"<demo url>\"]"; exit 1; }
echo "▸ script likhwa rahe…"
TXT=$(curl -s -X POST "$G/v1/ai/chat" "${H[@]}" -d "$(python3 -c '
import json,os,sys
print(json.dumps({"prompt":"voicenote","vars":{"business_name":sys.argv[1],"city":"Karachi",
 "demo_url":sys.argv[2],"agency_name":os.environ.get("AGENCY_NAME","WebDemo.pk"),
 "owner_name":os.environ.get("AGENCY_NAME","WebDemo"),"language":"roman-urdu",
 "user":"Write the spoken script now."}}))' "$BIZ" "$URL")" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("text",""))')
[[ -n "$TXT" ]] || { echo "❌ AI script nahi bana — make status / docker compose logs ai-gateway"; exit 1; }
echo "   📝 $TXT"
[[ $TEXTONLY == 1 ]] && exit 0
echo "▸ audio bana rahe…"
AUDIO=$(curl -s -X POST "$T/tts" -H "Content-Type: application/json" -d "$(python3 -c '
import json,sys;print(json.dumps({"text":sys.argv[1]}))' "$TXT")")
FN=$(echo "$AUDIO" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("filename",""))')
[[ -n "$FN" ]] || { echo "❌ TTS fail: $AUDIO"; echo "   docker compose --profile voicenote up -d"; exit 1; }
echo "   🎧 $FN ($(echo "$AUDIO" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("backend"))'))"
B64=$(base64 -w0 "calling/media/$FN" 2>/dev/null || base64 "calling/media/$FN" | tr -d '\n')
echo "▸ WhatsApp pe bhej rahe $PHONE…"
curl -s -X POST "http://127.0.0.1:${WAHA_PORT:-3000}/api/sendVoice" -H "Content-Type: application/json" \
  -H "X-Api-Key: ${WAHA_API_KEY}" -d "$(python3 -c '
import json,os,sys
print(json.dumps({"session":os.environ.get("WAHA_SESSION","default"),
 "chatId":sys.argv[1].replace("+","")+"@c.us","file":{"data":sys.argv[2],
 "mimetype":"audio/ogg; codecs=opu" if sys.argv[3].endswith(".ogg") else "audio/mpeg",
 "fileName":sys.argv[3]}}))' "$PHONE" "$B64" "$FN")" | head -c 400
echo; echo "✅ bhej diya"
