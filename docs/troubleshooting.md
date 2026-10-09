# Troubleshooting — lakshan → kaaran → hal

Har section pehle **check** karein (`make status` 90% kaam kar deta hai), phir neeche dekhein.

## `make status` kya batata hai
```
containers      → kaun chal raha hai / restart loop me
gateway         → providers + aaj ka bacha hua quota + deploy target
waha            → session state (active hona chahiye)
telegram        → bot token valid + chat_id set hai ya nahi
crm             → row count + status breakdown
hosting         → GitHub token/repo/Pages status
webhook         → n8n ka /webhook/waha reachable hai (000 = tunnel mar gaya)
.env sanity     → workflows jitni keys maangte hain, woh bhari hain ya nahi
```

## AI / gateway

**`all AI providers failed for task 'site:...' [gemini:429, groq:429]`**
Free tier cap. Check: `curl -s http://127.0.0.1:8787/healthz | jq .providers` → `today_left`.
Hal (kram se): (1) `MAX_LEADS_PER_DAY` 20→8, (2) `AI_DAILY_REQUESTS_PER_PROVIDER` 400→250
(429 se pehle ruk jaao), (3) doosri free provider chain me daalein (`AI_PROVIDER_CHAIN=gemini,groq`),
(4) `scripts/tune.sh` dekhein — usually yahi waqt hai jab aapko Groq key leni chahiye.

**`empty` attempts:** model ne kuch nahi likha. Zyadatar **thinking tokens** kha gaye (Gemini free).
Hal: `GEMINI_DISABLE_THINKING=true` (default) + `AI_MAX_TOKENS=16384`.

**`too small (2 KB) — likely truncated`**
Output limit chhota hai ya HTML beech me kat gaya. Hal: `AI_MAX_TOKENS` barhayein; Ollama pe
`OLLAMA_NUM_PREDICT`/`AI_MAX_TOKENS` 16384; prompt ke "18–48 KB" ko "12–24 KB" kar dein.

**`JSON parse failed` (classify)**
Chhote models JSON ke aas-paas galli-batchi karte hain. Gateway pehle extract karta hai, phir
1 repair pass, phir `coerce_keys` (line-based). Teeno fail → workflow **200 with `intent: unclear`**
lauTaata hai aur aapko Telegram par bhej deta hai — webhook kabhi 502 nahi karta (warna WAHA
retry storm bana deta). Check: `docker compose logs ai-gateway | grep repair`.

**Site har baar jaisi hi lagti hai**
Seed deterministic hai (lead key + din). Naye leads = naya seed. Same lead dobara = same design
(yeh feature hai: retry par design nahi badalna chahiye). Tez variety ke liye
`config/design-seeds.json` me palettes barha dein, ya `AI_TEMPERATURE` 0.85→1.0.

**Design generic/AI-jaisa (purple gradient) aa raha hai**
Model ne seed ignore kiya. Hal: `AI_TEMPERATURE` kam karein (0.6) — creative models ko "follow
instructions" par majboor karta hai — aur prompt me `rules_json` block ko upar move kar dein.
Gemini 2.5 Flash is kaam ke liye noticeably better than 8B-class models.

## Leads

**`osm_raw: 0`** — query kuch nahi lauta.
1. `docker compose exec ai-gateway python -c "import httpx;print(httpx.post('https://overpass-api.de/api/interpreter',data={'data':'[out:json][timeout:20];node[amenity=restaurant](24.80,67.00,24.86,67.07);out 3;'}).status_code)"` → 200?
2. Overpass mirror down ho sakta hai → `OVERPASS_MIRRORS` me `https://maps.mail.ru/osm/tools/overpass/api/interpreter` add karein (default me hai).
3. Nominatim `403` → `CONTACT_EMAIL` khaali hai (usage policy).

**Bahut kam leads / sab filtered**
`LEADS_RADIUS_METERS` 6000 → 9000; `MIN_HOTNESS` 40 → 15; `VERIFY_NO_WEBSITE=true` ko `false` karein
(test ke waqt). `config/campaigns/default.json` me `limit_per_area` barhayein.

**Geocode galat shehar de raha hai**
Nominatim ko aur specific dein: `"Gulshan-e-Iqbal Block 7, Karachi, Pakistan"`. Cache 90 din hai —
`rm gateway/data/geo.json` karke `docker compose restart ai-gateway`.

**Phone number ke baad bhi "no chat id"**
OSM me national format (`0300-1234567`) hota hai; gateway country dial code jodta hai
(`Radar.dial_code`). `country` galat/empty hai → code `92` nahi jodta. Check: `campaigns/default.json`
me `"country": "PK"`. `bash scripts/dryrun.sh` me normalized numbers print hote hain.

**Duplicate leads**
`lead_key` = `node/123456`. Same POI do baar nahi aata (CRM dedupe + scan exclude). Naya
`node` vs `way` id = do entry (OSM ka apna masla; rare). `make reset` se clean.

## WhatsApp / outreach

**`401 Unauthorized` from WAHA** — `WAHA_API_KEY` .env me badla magar container reload nahi hua:
`docker compose up -d waha n8n`.

**`Session not found`** — `WAHA_SESSION` ≠ actual session name. `curl -H "X-Api-Key: ..." localhost:3000/api/sessions`.

**Send hota hai magar customer tak nahi jaata (1 tick)**
Number flagged, ya recipient ne block kiya, ya naye number se bulk. **Ruk jayein.** `docs/anti-ban.md`.

**Bot replies nahi karta** (aap expect kar rahe the)
`AUTO_REPLY_ENABLED=true`? `confidence < 0.55`? `intent` risky list me (`not_interested`,
`handoff_needed`, `objection_price`, `question_other`)? `ai_reply_count >= AUTO_REPLY_MAX_PER_THREAD`?
Sab 03 ke `Decide` node me JSON me nazar aate hain (output dekhein) — `guard` field batata hai.

**Replies double aa rahe**
1. `ACK 200` node webhook ke **turant baad** hona chahiye (end me nahi) — warna WAHA timeout pe
   dobara POST karta hai.
2. WAHA hook me `message` **aur** `message.any` dono hain → same message 2 baar.
   `WHATSAPP_HOOK_EVENTS=message` rakhein.
3. do `n8n` instances chal rahe (compose + host pe purana) — `docker ps` / `ps aux | grep n8n`.

**Webhook 404**
`WEBHOOK_URL` ke end me `/` na ho (compose `${WEBHOOK_URL%/}webhook/waha` handle karta hai),
workflow **activate** hona chahiye (test URL ≠ production URL). `make status` me HTTP code dekhein.

## CRM / deploy

**`BASEROW_TABLE_ID` khaali / 404 on rows** — `make crm` chalayein, phir `docker compose up -d n8n`.
Token 401 → Baserow UI me token regenerate (self-hosted install me DB reset se tokens ud jaate hain).

**Pages 404 for 1-2 minutes** — build queue. Gateway pehle poll karta hai (`live: false` aaye to
CRM status `deploy_pending` rehta hai → workflow 02 `demo_url` na hone par send nahi karta).
`make pages` + `.nojekyll` commit karein.

**Site deploy ho gayi magar CSS toota** — `strip_danger()` ne koi zaroori external cheez kaat di?
Allowed hosts: fonts.googleapis/gstatic, picsum.photos, google maps, wa.me. Custom CDN chahiye to
`gateway/sitegen.py:ALLOWED_EXT` me add karein (test: `make test`).

**`demo_failed: no valid JSON response`** — gateway down ya `GATEWAY_URL` galat. Container
network me `http://ai-gateway:8787` hi hona chahiye (localhost nahi — n8n container ke liye localhost
uska andar hai).

## Logs — kaunsi file kahan
```bash
docker compose logs -f --tail=80 ai-gateway   # AI/leads/deploy (sabse zyada kaam ki)
docker compose logs -f waha                   # WhatsApp protocol/ban warnings
docker compose logs -f n8n                    # workflow execution errors
docker compose logs baserow                   # CRM API
# n8n executions: UI → Executions → (workflow) → ek click me node-by-node JSON
```
Gateway debug: `LOG_LEVEL=DEBUG docker compose up -d ai-gateway`.

## "Kal subah kuch nahi hua" — 4 minute ka diagnosis
1. `make status` (containers + webhook + quota).
2. n8n → Executions: kya workflow chala hi? Agar nahi → schedule trigger ka node on hai?
   Server time/`GENERIC_TIMEZONE` theek?
3. Chala magar 0 leads → OSM area/campaign config dekhein; `make dryrun` se prove karein.
4. Leads mile magar 0 sent → 02: `demo_url` hai? `next_action_at` future me? cap hit?
   `send_failed`? — sab `Log send` node ke output me.
5. Kuch dikhe nahi → `make report` ka funnel aapko bata dega ke pipeline kis gate par ruka.

## Sabse aam 3 self-inflicted galtiyan (mere dekhe hue)
1. `.env` edit ke baad `docker compose up -d` nahi kiya → purani env containers me.
2. GitHub PAT me `Contents: write` nahi (Default=read-only) → deploy 403, CRM me `demo_failed`.
3. Telegram bot ka `chat_id` `0` chhod diya → koi alert nahi, isliye 3 din koi reply nahi dikha.
