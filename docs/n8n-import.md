# n8n import + pehla run (2 min, common galtiyan)

## Import
1. `http://localhost:5678` → **Workflows** → top-right **⋯ → Import from File** →
   `n8n/workflows/01-leads-and-demos.json` (phir 02, 03, 04, 05).
2. Import ke baad har workflow **Save** kar dein (Ctrl+S / ⌘S).
3. Koi credential create nahi karni (Gmail node chhoda hai — disabled).

Files `tools/build_workflows.py` se banti hain. Aap JSON ko seedha edit kar sakte hain (n8n UI
se ya file se) — koi lock nahi hai. Agar aap builder badlein to:
```bash
make build && make validate      # validate = shape + $env keys + expression balance
```

## Pehla run (haath se)
1. `01-leads-and-demos` kholein → **Execute Workflow** (bottom-right).
   Chalna: Schedule trigger UI me "test" mode me fire hota hai.
2. Nodes ek-ek karke green honge. Har node par click karke **Output** dekhein:
   - `Radar: scan OSM` → `stats: {osm_raw: 120, no_website: 14...}`
   - `AI: generate demo site` → `ok: true`, `bytes: ~28000`, `design: {...}`
   - `Deploy to Pages` → `live: true`, `url: https://...`
   - `CRM: save demo · create` → naya row with `id`
3. Phir `02-outreach` → Execute (yeh aapke *real* numbers bhejega — `MAX_OUTREACH_PER_DAY`
   pehle `2` kar dein test ke liye, ya `scripts/dryrun.sh --to <apna number>` behtar hai).
4. `03-replies` **activate** karein (toggle) — yeh sirf webhook se chalta hai.
5. Kisi doosre number se us demo link par ek "interested" message bhejein → Telegram alert
   aana chahiye + AI ka reply. Yeh hi poore system ka sachcha end-to-end test hai.

## Schedule ko manually chalaana (baad me)
n8n UI me workflow khol kar **Execute workflow** — ya `curl`:
```bash
# 01 ko API se trigger karna ho to: workflow me ek "Ping" Webhook node add kar lein,
# ya seedha gateway ko:
curl -s -X POST http://127.0.0.1:8787/v1/leads/scan -H "X-Gateway-Token: $(grep ^GATEWAY_TOKEN .env|cut -d= -f2)" \
     -H 'Content-Type: application/json' -d '{"campaign":"karachi","limit":5}' | head -c 400
```

## "⚠ Node problem" — 4 wajahain
| Error | Kaaran | Hal |
|---|---|---|
| `Cannot read properties of undefined (reading 'keys')` at `Radar: scan OSM` | `Read CRM` node fail hua (Baserow down / `BASEROW_TABLE_ID` khaali) | `make crm` → `docker compose up -d n8n` |
| `401 Unauthorized` (gateway nodes) | `GATEWAY_TOKEN` .env me naya hai lekin container reload nahi hua | `docker compose up -d ai-gateway n8n` |
| `404 session not found` (WAHA) | session start nahi hua | `bash scripts/waha-login.sh` |
| `Workflow with ID "..." not found` | (purane version ki baat hai — ab CRM inline hai, ye error nahi aana chahiye) | `make build` karke naya JSON import karein |
| Expression `{{ $env.X }}` = empty | `.env` me `X` khaali hai | `make status` saari khaali keys ginta hai |
| `Execution blocked: missing credentials` | 02 ka Gmail node enable kar diya bina credential | node disable kar dein ya SMTP credential bana lein |

## Environment variables kahan se aate hain
n8n container ko poora `.env` **pass-through** milta hai (`environment:` block in compose) isliye
har workflow `$env.GATEWAY_URL`, `$env.BASEROW_TABLE_ID`, … padh leta hai. Naya key add karna ho to:
1. `.env` + `.env.example` me daalein, 2) `docker-compose.yml` ke n8n `environment:` me line add
karein, 3) `docker compose up -d n8n`. *(Sirf .env me daalne se container ko nahi milti — yeh
#1 confusion hai.)*

## Timezones
`GENERIC_TIMEZONE=Asia/Karachi` set hai — cron isi ke mutabiq chalega. Server UTC ho to bhi theek.
Test: 01 node khol kar schedule par "Next execution" dekhein.

## Backup/restore
- **Backup:** n8n UI → workflow → Download (JSON) — repo me `n8n/workflows/*.json` bhi hain;
  aur `docker compose cp n8n:/home/node/.n8n ./n8n-data-backup` (credentials ka encrypted store).
- **Restore:** naya box → repo clone → `.env` wapas → `docker compose up -d` → workflows import
  (credentials ka encryption key `.env` me hai, isliye n8n credentials bhi wapas padh lega).

## Upgrade
```bash
docker compose pull && docker compose up -d
python3 tools/build_workflows.py && python3 tools/validate_workflows.py   # n8n node versions badle to
```
n8n ke bade version par HTTP/IF/Switch node ka `typeVersion` barh sakta hai — import purane
typeVersion par bhi chalta hai (n8n khud migrate karta hai), magar `make test` + `make validate`
chalana aadat bana lein.
