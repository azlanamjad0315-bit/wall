#!/usr/bin/env python3
"""baserow_setup.py — CRM ban jaaye, haath se 26 fields nahi banane.

    python3 tools/baserow_setup.py --url http://localhost:8100 --token <TOKEN>

Kya karta hai:
  1. database 'DemoSites' + table 'leads' banata hai (already ho to reuse)
  2. saare fields create karta hai (jo type Baserow na maane to 'text' pe fallback)
  3. BASEROW_TABLE_ID ko .env me likh deta hai (workflow usi ko padhte hain)

Token kahan se: Baserow UI -> right top profile -> "Admin links" -> API tokens -> Add new token.
Ya: http://localhost:8100/login -> signup -> Settings -> API Token.

    --reset     saare rows delete (naya campaign shuru karne se pehle)
    --dry-run   kuch nahi banega, sirf plan dikhayega
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (field name, Baserow type) — names are the contract with the n8n workflows; renaming here
# means renaming every reference in tools/build_workflows.py too.
FIELDS: list[tuple[str, str]] = [
    ("lead_key", "text"), ("business_name", "text"), ("category", "text"),
    ("category_label", "text"), ("city", "text"), ("area", "text"), ("country", "text"),
    ("address", "long_text"), ("phone", "text"), ("whatsapp", "text"), ("email", "text"),
    ("website", "text"), ("rating", "text"), ("reviews", "text"), ("hours", "text"),
    ("hotness", "number_integer"), ("status", "text"), ("intent", "text"),
    ("language", "text"), ("price_label", "text"), ("demo_slug", "text"), ("demo_url", "text"),
    ("design", "text"), ("site_bytes", "number_integer"), ("ai_provider", "text"),
    ("deploy_target", "text"), ("deploy_note", "text"), ("outreach_channel", "text"),
    ("last_outbound", "long_text"), ("last_ai_reply", "long_text"), ("sent_at", "text"),
    ("last_inbound", "text"), ("next_action_at", "text"), ("outbound_count", "number_integer"),
    ("reply_count", "number_integer"), ("ai_reply_count", "number_integer"),
    ("followup_count", "number_integer"), ("payment_info_sent", "text"), ("paid", "text"),
    ("amount", "text"), ("source", "text"), ("emoji", "text"),
    ("notes", "long_text"), ("ai_notes", "long_text"),
]
FALLBACK_TYPE = "text"


def api(url: str, token: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Token {token}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body.strip().startswith(("{", "[")) else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()[:400]
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw}
    except Exception as e:                                       # connection refused etc
        raise SystemExit(f"❌ Baserow reachable nahi ({url}): {e}\n"
                         f"   docker compose --profile baserow up -d  ->  phir 'docker compose ps' chalayein")


def patch_env(updates: dict[str, str]) -> None:
    env = ROOT / ".env"
    if not env.exists():
        (ROOT / ".env.example").read_text()
        env.write_text((ROOT / ".env.example").read_text())
        print("   (.env nahi tha — .env.example se copy kar diya)")
    text = env.read_text()
    for k, v in updates.items():
        if re.search(rf"^{re.escape(k)}=", text, re.M):
            text = re.sub(rf"^{re.escape(k)}=.*$", f"{k}={v}", text, flags=re.M)
        else:
            text += f"\n{k}={v}"
    env.write_text(text)
    print(f"   .env update: {', '.join(f'{k}={v}' for k, v in updates.items())}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8100")
    ap.add_argument("--token", default="")
    ap.add_argument("--db", default="DemoSites")
    ap.add_argument("--table", default="leads")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true", help="all rows delete karo (table nahi mitao)")
    ap.add_argument("--env", default="", help=".env ka path (default: repo/.env)")
    a = ap.parse_args()
    url = a.url.rstrip("/")
    token = a.token or ""
    print(f"Baserow: {url}   fields: {len(FIELDS)}")

    if a.dry_run:
        for n, t in FIELDS:
            print(f"  {n:<18} {t}")
        print("(dry-run — kuch nahi banaya)")
        return 0
    if not token:
        print("\n❌ --token chahiye.\n"
              "   Baserow UI (http://localhost:8100) -> sign up -> profile icon -> Settings ->\n"
              "   'API tokens' -> Add new token -> copy -> phir:\n"
              "   python3 tools/baserow_setup.py --token <TOKEN>\n")
        return 2

    st, dbs = api(f"{url}/api/databases/", token)
    if st != 200:
        print(f"❌ token reject hua ({st}): {json.dumps(dbs)[:200]}")
        return 2
    db = next((d for d in (dbs if isinstance(dbs, list) else dbs.get("results", []))
               if d.get("name") == a.db), None)
    if not db:
        st, db = api(f"{url}/api/databases/", token, "POST", {"name": a.db})
        if st not in (200, 201):
            print(f"❌ database nahi bani ({st}): {db}")
            return 2
        print(f"✓ database '{a.db}' (id {db.get('id')})")
    else:
        print(f"✓ database '{a.db}' already thi (id {db.get('id')})")
    db_id = db["id"]

    st, lists = api(f"{url}/api/databases/{db_id}/tables/", token)
    tables = lists if isinstance(lists, list) else lists.get("results", [])
    tab = next((t for t in tables if t.get("name") == a.table), None)
    if not tab:
        st, tab = api(f"{url}/api/databases/{db_id}/tables/", token, "POST",
                      {"name": a.table, "order": 1})
        if st not in (200, 201):
            print(f"❌ table nahi bani ({st}): {tab}")
            return 2
        print(f"✓ table '{a.table}'")
    else:
        print(f"✓ table '{a.table}' already thi")
    table_id = tab["id"]

    st, fl = api(f"{url}/api/tables/{table_id}/fields/", token)
    have = {f.get("name") for f in (fl if isinstance(fl, list) else fl.get("results", []))}
    made, skipped, failed = 0, 0, []
    for name, ftype in FIELDS:
        if name in have:
            skipped += 1
            continue
        st, res = api(f"{url}/api/tables/{table_id}/fields/", token, "POST",
                      {"name": name, "type": ftype, "field_options": {}})
        if st not in (200, 201):
            st, res = api(f"{url}/api/tables/{table_id}/fields/", token, "POST",
                          {"name": name, "type": FALLBACK_TYPE, "field_options": {}})
        if st in (200, 201):
            made += 1
        else:
            failed.append(f"{name}: {str(res)[:120]}")
    print(f"✓ fields: {made} nayi, {skipped} pehle se thi"
          + (f", {len(failed)} FAILED" if failed else ""))
    for f in failed:
        print("   ! " + f)

    if a.reset:
        st, rows = api(f"{url}/api/database/rows/table/{table_id}/?limit=1000", token)
        ids = [r["id"] for r in (rows.get("results") or [])]
        for i in ids:
            api(f"{url}/api/database/rows/table/{table_id}/{i}/", token, "DELETE")
        print(f"✓ {len(ids)} rows delete ho gayi")

    if not failed:
        if a.env:
            env_path = Path(a.env)
            env_path.write_text((ROOT / ".env.example").read_text()) if not env_path.exists() else None
        patch_env({"BASEROW_TABLE_ID": str(table_id)})
        print(f"""
✅ CRM ready. Ab:
   1) .env me BASEROW_TOKEN daal dein (jo upar use kiya) — compose use kar ke n8n restart:
        docker compose up -d n8n
   2) n8n import karein (README step 5) — workflows ko BASEROW_TABLE_ID mil jaayega.
   3) CRM dekhne ke liye: {url}/workspace/1/database/{db_id}/
""")
    else:
        print("❌ kuch fields nahi banin — Baserow version check karein, ya manually "
              f"{url} me table '{a.table}' me ye fields bana lein: " + ", ".join(n for n, _ in FIELDS))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
