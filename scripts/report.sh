#!/usr/bin/env bash
# report.sh — Baserow se funnel numbers terminal me (kuch nahi badalta)
set -uo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && { set -a; . ./.env; set +a; }
BURL="${BASEROW_URL:-http://baserow:8000}"; [[ "$BURL" == *baserow:8000* ]] && BURL="http://127.0.0.1:${BASEROW_PORT:-8100}"
[[ -n "${BASEROW_TABLE_ID:-}" && -n "${BASEROW_TOKEN:-}" ]] || { echo "BASEROW_TABLE_ID/BASEROW_TOKEN .env me daalein (bash scripts/crm.sh)"; exit 1; }
curl -s "$BURL/api/database/rows/table/$BASEROW_TABLE_ID/?limit=10000&user_field_names=true" \
  -H "Authorization: Token $BASEROW_TOKEN" | python3 - "$PRICE_LABEL" <<'PY'
import json, sys, collections
rows = json.load(sys.stdin).get("results", [])
price = sys.argv[1] if len(sys.argv) > 1 else ""
def g(r, k): 
    v = r.get(k)
    return "" if v is None else v
print(f"\n  {len(rows)} leads in CRM\n  " + "─"*46)
c = collections.Counter(g(r,"status") or "-" for r in rows)
for k, v in sorted(c.items(), key=lambda x: -x[1]):
    bar = "█" * max(1, int(v / max(1, max(c.values())) * 22))
    print(f"  {k:<20} {v:>4}  {bar}")
touched = sum(v for k,v in c.items() if k in ("contacted","replied","interested","negotiating","paid",
              "followup_sent","revision_requested","stalled","closed_noreply","payment_claimed"))
replied = sum(1 for r in rows if int(g(r,"reply_count") or 0) > 0)
paid = sum(1 for r in rows if str(g(r,"paid")).lower()=="yes")
intent = collections.Counter(g(r,"intent") for r in rows if g(r,"intent"))
print("  " + "─"*46)
print(f"  touched {touched} | replies {replied}"
      + (f" ({round(replied/touched*100)}%)" if touched else "")
      + f" | paid {paid}" + (f" ≈ {paid} × {price}" if paid and price else ""))
if intent: print("  intents:", ", ".join(f"{k}={v}" for k,v in intent.most_common()))
hot = [r for r in rows if str(g(r,"status")) in ("interested","negotiating","payment_claimed","handoff","revision_requested")]
if hot:
    print("\n  ➜ FOLLOW UP KAREIN (aaj):")
    for r in sorted(hot, key=lambda x: -int(g(x,"hotness") or 0))[:10]:
        print(f"     • {g(r,'business_name'):<28} {g(r,'status'):<18} wa.me/{g(r,'whatsapp') or g(r,'phone')}")
demos = [r for r in rows if g(r,"demo_url")]
if demos:
    print(f"\n  {len(demos)} live demos, e.g.")
    for r in demos[:3]: print(f"     • {g(r,'business_name')}: {g(r,'demo_url')}")
print()
PY
