#!/usr/bin/env bash
# tune.sh — funnel data padh kar batata hai ke AGAAY kya badalna chahiye (free tier friendly)
set -uo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && { set -a; . ./.env; set +a; }
BURL="${BASEROW_URL:-http://baserow:8000}"; [[ "$BURL" == *baserow:8000* ]] && BURL="http://127.0.0.1:${BASEROW_PORT:-8100}"
[[ -n "${BASEROW_TABLE_ID:-}" ]] || { echo "pehle: bash scripts/crm.sh"; exit 1; }
curl -s "$BURL/api/database/rows/table/$BASEROW_TABLE_ID/?limit=10000&user_field_names=true" \
  -H "Authorization: Token $BASEROW_TOKEN" | python3 - <<'PY'
import json, os, collections
rows = json.load(sys.stdin).get("results", [])
g = lambda r, k: ("" if r.get(k) is None else str(r[k]))
n = len(rows)
touched = [r for r in rows if g(r,"status") in ("contacted","replied","interested","negotiating","paid",
           "followup_sent","revision_requested","stalled","closed_noreply","payment_claimed")]
replied = [r for r in touched if int(g(r,"reply_count") or 0) > 0]
inter = [r for r in rows if g(r,"status") in ("interested","payment_claimed","negotiating")]
paid  = [r for r in rows if g(r,"paid").lower()=="yes"]
failed= [r for r in rows if g(r,"status")=="demo_failed"]
fallback = [r for r in rows if "fallback" in g(r,"ai_notes")]
rate = (len(replied)/len(touched)*100) if touched else 0
tips, say = [], print
say(f"\n  📈 tune — {n} leads, {len(touched)} touched, reply-rate {rate:.0f}%, interested {len(inter)}, paid {len(paid)}\n  " + "─"*56)
if n == 0:
    say("  CRM khaali: pehle make dryrun, phir workflow 01 ka 'Execute workflow' dabayein.")
if len(failed) > n*0.2 and n:
    say(f"  ⚠️  {len(failed)} demos FAIL hue ({len(failed)*100//n}%). .env me GEMINI_API_KEY/GROQ_API_KEY check;")
    say("      docker compose logs ai-gateway | grep -i 'providers failed'  → mostly RPD cap. Hal: MAX_LEADS_PER_DAY kam karein.")
if len(fallback) > 3:
    say(f"  ⚠️  AI message {len(fallback)} baar safety-gate fallback bana — model rules tod raha hai.")
    say("      Hal: prompts/outreach-roman-urdu.md ka word-limit 45→60 karein, ya AI_TEMPERATURE 0.85→0.7.")
if touched and rate < 12:
    say("  💬 reply rate kam. Sabse bara lever = pehli line. Test karein:")
    say("      • unka mohalla + ek specific kami (menu nahi / timings nahi) mention karein")
    say("      • 'free preview' line rakhein, price bilkul na likhein")
    say("      • link ke baad koi sawal NAHI (sawal = extra effort = ignore)")
    say("      2 hafte tak 2 variants A/B karein — status column me 'variant_a' tag karke")
if touched and rate >= 20 and len(paid) == 0:
    say("  💰 replies achi, paid zero → payment step slow hai. Interested pe FORAN Telegram alert +")
    say("      2 ghante me site live. Reply ka wait na karein; voice note bhejein (make voice-note).")
if len(touched) < 15:
    say("  📣 volume kam: config/campaigns/default.json me 6-10 aur areas daalein (1 line each),")
    say("      MAX_OUTREACH_PER_DAY 25 → 30 se zyada NAHI (ban risk).")
if len(inter) and not len(paid):
    say("  🤝 'interested' ke baad: payment screenshot pehle, site phir — ya 50% advance. Yeh aapka kaam hai, AI ka nahi.")
if os.environ.get("AI_PROVIDER_CHAIN","").startswith("ollama"):
    say("  🐢 sirf Ollama: quality/size limit padega. Free Gemini key le lein (1 min) — chain me pehle rakhain.")
tips = [t for t in tips if t]
say("  ✅ theek chal raha hai — roz chalta rahein." if not (failed or fallback or rate<12 or len(touched)<15) else "")
say("\n  Yaad: outreach rate (MAX_OUTREACH_PER_DAY) aur delay barhana 'zyada paisa' nahi, 'zyada ban' hai.\n")
PY
