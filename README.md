# DemoSites AI — local businesses ko AI demo website bechne wala 100% free system

**Kya hai yeh:** roz subah apne aap aas-paas ki dukaano ko dhoondhta hai jinki website nahi hai,
har ek ke liye AI se ek modern 1-page site banata hai, free hosting par live karta hai, business
owner ko Roman Urdu (ya English) me WhatsApp par demo link bhejta hai, reply aane par AI usko
classify karta hai, aapko Telegram par alert deta hai, aur sab kuch ek CRM me likhta rehta hai.

**Aapka kaam:** roz subah 1 Telegram message padhna, aur jiska reply "haan" ho usse payment lena.
Setup ke din ~40 minute. Bas.

**Paisa:** $0/month. Har cheez free — n8n (self-hosted), Gemini/Groq free tier ya Ollama, GitHub
Pages, Baserow, Telegram, WAHA (self-hosted WhatsApp), OpenStreetMap. Koi card nahi, koi subscription
nahi. (Jo cheezein *free nahi* ho saktin — phone minutes — woh `docs/free-vs-paid.md` me saaf-saaf
likha hai.)

```
OSM/Overpass ─┐
              ├─▶ [01] ai-gateway ──▶ AI site HTML ──▶ GitHub Pages ──▶ demo URL
CRM (Baserow) ┘                                             │
                                                            ▼
                          [02] AI Roman-Urdu message ──▶ WhatsApp (WAHA) / Gmail
                                                            │ reply
                                                            ▼
CRM status ◀── [04] follow-ups + daily report ◀── [03] classify ──▶ Telegram alert 🔥
```

---

## 0. Kya-kya chahiye (sirf 3 account)

| Chiz | Kyun | Kitna time | Free? |
|---|---|---|---|
| **GitHub account** | demo sites host karne ke liye (GitHub Pages) | 2 min | ✅ |
| **Google account** → [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | AI (Gemini free tier) | 1 min | ✅ card nahi chahiye |
| **Telegram** → [@BotFather](https://t.me/BotFather) se bot | aapko roz ka alert | 2 min | ✅ |
| Spare Android phone/SIM | WhatsApp number (main number NAHI) | — | ✅ |

Server: koi bhi Linux/Mac box with **Docker**, 2 CPU / 4 GB RAM (Ollama local AI chahiye to 8 GB+).
Purana laptop bhi chalega. VPS lena hai to ~$5 wala bhi kaam karta hai, lekin zaroori nahi.

---

## 1. Repo + containers

```bash
git clone <repo> && cd wall          # ya folder copy karein
cp .env.example .env
make up                              # dirs (permissions) + build + up  — ek command
docker compose ps                    # n8n, ai-gateway, waha = running
```

**Test karein:**
```bash
curl -s http://127.0.0.1:8787/healthz | python3 -m json.tool
```
Sahi jawab: `"ok": true`, `"providers": [...]` me kam se kam ek provider. Agar
`"providers": "NONE..."` likha hai → abhi AI key nahi bhari (step 2).

Agar `docker compose ps` me waha restart kar raha hai → koi baat nahi, WhatsApp abhi login nahi hua.

> **Aur bhi aasan:** `bash scripts/bootstrap.sh` — yeh `.env` bana deta hai, 3 secrets
> (n8n encryption key, gateway token, WAHA API key) **khud generate** karke bhar deta hai, folders
> permission-safe banata hai, build + up karta hai, aur phir batata hai ke aage kya bharna hai.
> Sirf `docker compose up -d` chalane par `./n8n/data` root-owned ban kar n8n ko restart loop me
> daal sakta hai — isliye `make up`/`make dirs` behtar hai.

---

## 2. `.env` bharin — sirf 6 khaane zaroori hain

`.env` kholen aur yeh daalein (baaki defaults pe chalta hai):

```bash
GEMINI_API_KEY=AIza...              # aistudio.google.com/apikey se copy
CONTACT_EMAIL=aap@gmail.com         # Nominatim/Overpass ko apna contact dena zaroori hai
AGENCY_NAME=AliWebStudio            # aapka brand — har demo site ke footer me aayega
CONTACT_PHONE=923001234567          # AAPKA WhatsApp number (bina + ke, E.164)
WHATSAPP_NUMBER=923001234567        # same
TELEGRAM_BOT_TOKEN=123456:ABC-DEF   # @BotFather se
TELEGRAM_CHAT_ID=987654321          # @userinfobot se, ya neeche wale tareeqe se
```

**Telegram chat id kaise mile:** bot ko `/start` likh dein, phir:
```bash
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" | python3 -m json.tool | grep chat -A3
```

**Price aur payment (aapki dukaan):** `.env` me yeh 3 lines bhi daal dein —
```bash
PRICE_LABEL=PKR 15,000 one-time
PAY_METHODS=Easypaisa: 03XX-XXXXXXX | JazzCash: 03XX-XXXXXXX
PAY_ACCOUNT_NAME=Ali Raza
```
> `XXXX` wali jagah apne asli numbers. `grep -rn XXXX .` se check kar lein ke kahin placeholder na reh gaya.

**Test karein:**
```bash
docker compose restart ai-gateway && sleep 3
curl -s -H "X-Gateway-Token: $(grep ^GATEWAY_TOKEN .env | cut -d= -f2)" http://127.0.0.1:8787/healthz | python3 -m json.tool
```
`providers` me `gemini` aana chahiye aur `today_left` ~400 (yeh aapka roz ka suraksha cap hai —
free tier ke RPD se kam rakha gaya hai taake 429 na aaye).

Ek asli AI call test:
```bash
curl -s -X POST http://127.0.0.1:8787/v1/ai/chat \
  -H "X-Gateway-Token: $(grep ^GATEWAY_TOKEN .env | cut -d= -f2)" -H 'Content-Type: application/json' \
  -d '{"system":"Reply with exactly: OK GEMINI WORKS","user":"go","max_tokens":20}'
```

---

## 3. Jahan se demo host honge: GitHub Pages (free, 3 min)

1. GitHub par **public** repo banayein: `demo-sites` (khaali, README ke saath).
2. Repo → Settings → Pages → **Source: Deploy from a branch** → `main` / `/ (root)` → Save.
3. **Settings → Developer settings → Fine-grained tokens** → New token:
   - Repository access: *Only select repositories* → `demo-sites`
   - Permissions: **Contents: Read and write**
   - Copy karein.
4. `.env`:
```bash
DEPLOY_TARGET=github-pages
GITHUB_REPO=aapka-username/demo-sites
GITHUB_TOKEN=github_pat_...
PAGES_BASE_URL=https://aapka-username.github.io/demo-sites
```
5. Ek baar `.nojekyll` file commit karein (build 10x tez ho jaata hai):
```bash
curl -X PUT -H "Authorization: Bearer $GITHUB_TOKEN" -H "Content-Type: application/json" \
  https://api.github.com/repos/aapka-username/demo-sites/contents/.nojekyll \
  -d '{"message":"init","content":""}'
```

Har site ka link aisa banega: `https://<user>.github.io/demo-sites/bismillah-bakers-dha-1a2b/`

**Test karein:** `make pages` — Pages status `built`, `.nojekyll` `200`, aur sample URL `200` aana chahiye.

> Cloudflare Pages use karna hai (instant deploy, koi build wait nahi) to `docs/hosting.md` —
> system me `DEPLOY_TARGET=cf-pages` ka support pehle se hai.

---

## 4. WhatsApp: WAHA login (2 min) — ⚠️ spare number se

```bash
docker compose up -d waha
bash scripts/waha-login.sh        # QR dikhaayega → phone se scan: WhatsApp → Linked Devices
docker compose ps waha
```
**Test karein:**
```bash
bash scripts/status.sh            # "session active" aana chahiye
curl -s -X POST http://127.0.0.1:3000/api/sendText -H "X-Api-Key: $(grep ^WAHA_API_KEY .env | cut -d= -f2)" \
  -H 'Content-Type: application/json' \
  -d '{"session":"default","chatId":"923001234567@c.us","text":"WAHA test ✅"}'
```
Apne hi number pe message aana chahiye.

**Zaroori baatein (number bachane ke liye):**
- Purana/spare SIM use karein, WhatsApp Business account behtar hai.
- Pehle 7 din **sirf 10 message/day**, phir 20, phir 25-30. `MAX_OUTREACH_PER_DAY` se control hota hai.
- Reply aane par insaan baat aage badhaye — AI 2 reply ke baad chup ho jaata hai (default).
- `docs/anti-ban.md` poora padh lein. Yeh system aapke number ko risk me daal sakta hai; limit hi asal feature hai.

---

## 5. CRM (Baserow) — haath se fields banane ki zaroorat nahi

```bash
docker compose --profile baserow up -d
sleep 45 && docker compose ps baserow
```
1. Browser me `http://localhost:8100` → **Sign up** (local account, koi email verification nahi).
2. Right-top profile → **Settings → API Token** → copy.
3. Bas ab:
```bash
bash scripts/crm.sh --token <WOH_TOKEN>
```
Yeh database `DemoSites`, table `leads`, **44 fields** bana dega aur `BASEROW_TABLE_ID` ko
`.env` me khud likh dega. Phir `docker compose up -d n8n` (env reload).

Google Sheets prefer karte hain? `docs/crm.md` me 4 workflows me se kaunse nodes badalne hain, line-by-line.

**Test karein:** `bash scripts/report.sh` → `0 leads in CRM` aana chahiye.

---

## 6. n8n workflows import karein (3 min)

1. `http://localhost:5678` kholen → account banayein.
2. Left menu → **Workflows → ⋯ → Import from File** → file chunein (repo se `n8n/workflows/`):
   - `01-leads-and-demos.json`
   - `02-outreach.json`
   - `03-replies.json`
   - `04-followup-report.json`
   - (`05-voice-note-optional.json` — marzi)
3. Har workflow khulte hi **Save** (Ctrl+S) kar dein.
4. Sab me **koi credential nahi** banani — sab keys containers ke environment se aati hain
   (isliye import 100% clean hota hai). Sirf `02` ka *Gmail* node disabled hai; email channel
   chahiye to: Credentials → SMTP → `smtp.gmail.com:465` + Gmail **App Password** → node enable.

Agar koi node laal dikhaye → `docs/n8n-import.md` (aam 4 wajahain).

---

## 7. Webhook: WAHA → n8n (default me **kuch karna nahi**)

WAHA aur n8n ek hi docker network par hain, isliye hook **andar hi** chal jaata hai:
`.env` me `WAHA_HOOK_URL=http://n8n:5678/webhook/waha` (default) — **koi domain, account, ya
tunnel ki zaroorat nahi.** Yeh sabse surakshit bhi hai (kuch public nahi).

**Test karein** (03 activate hone ke baad):
```bash
bash scripts/status.sh | grep -A2 webhook     # HTTP 200/400/404 = reachable ✓
curl -s -X POST http://localhost:5678/webhook/waha -H 'Content-Type: application/json' -d '{"event":"message"}'
```
`{"ok":true}` aaye to n8n ka `03-replies` theek se sun raha hai.

Kab aapko **public URL** chahiye?
- n8n VPS par hai aur WAHA ghar pe (alag machines) — ya
- aap n8n UI/CRM remotely phone se dekhna chahte hain.
```bash
bash scripts/tunnel.sh   # Cloudflare quick tunnel (free, URL restart pe badalta hai)
```
Yeh `WEBHOOK_URL` + `WAHA_HOOK_URL` + `N8N_EDITOR_BASE_URL` teeno update kar deta hai.
Poori tafseel + permanent free option (apne domain ke saath): `docs/tunnel.md`.

## 8. Sabse zaroori test: dry-run (kuch send NAHI hota)

```bash
make dryrun                              # ya:
bash scripts/dryrun.sh --area "Gulshan-e-Iqbal, Karachi" --niche restaurant
```
Yeh 5 kaam karta hai: lead scan → AI site → deploy → message likhna → sab print karna.

**Check karein:**
1. `/tmp/dryrun-demo.html` browser me kholein → **mobile view (390px)** → khana/timings/WhatsApp button sab kaam karein.
2. Deploy step me `"live": true` aaye.
3. Message: 45 words se kam, pehli line me unka naam, **price na likha ho**, link alag line par.
4. Neeche footer me aapka DEMO strip + aapka WhatsApp link.

Ab asli-but-safe test — generated message **apne hi number** pe:
```bash
bash scripts/dryrun.sh --to 923001234567
```
Phone pe kholein. Agar aap khud is message ka jawab dete — dete? Nahi to `prompts/outreach-roman-urdu.md`
badlein, `make build` ki zaroorat nahi (prompts file runtime par padhi jaati hai), aur dobara test karein.

---

## 9. Chalaayein (ON)

n8n me:
1. `01-leads-and-demos` → **Activate** (toggle, top-right). Pehla run khud aaj subah 6 baje hoga;
   abhi ke liye node ke andar **Execute workflow** dabayein.
2. `02-outreach` → **Activate**
3. `03-replies` → **Activate** (yeh webhook hai, message aate hi chalega)
4. `04-followup-report` → **Activate**

**Test:** workflow 01 ke baad `bash scripts/report.sh` — rows `demo_ready` me dikhne chahiye.
Workflow 02 ke baad aapke CRM me `contacted`, aur 5-10 minute me pehla message aapke spare number
ke outbox me.

---

## 10. Roz ka aapka kaam (5 minute)

| Waqt | Kya |
|---|---|
| Subah 6:00 | Kuch nahi (01 chalta hai) |
| 11:00 / 17:00 | Kuch nahi (02 bhejta hai) |
| Din me | **Telegram alert** 🔥 `INTERESTED` → wa.me link kholen → 2 line mein deal close karein |
| Raat 21:00 | Telegram par `📊 Daily report` — reply rate, interested, paid, aur kal ka kaam |

**Paisa lene ke baad:** CRM me us row ka `paid` field `yes` kar dein (ya Telegram alert se
copy-paste). Bas — `04` uske aage ka reminder band kar deta hai.

---

## 11. Prompt ko behtar karna (asli growth yahi hai)

| File | Isse badalta hai |
|---|---|
| `prompts/site-generator.md` | Site ka design/quality. Yahi aapka product hai. |
| `prompts/outreach-roman-urdu.md` / `-english.md` | Pehla message = reply rate |
| `prompts/reply-classifier.md` | AI ka jawab, kaunse case me aapko bulana hai |
| `prompts/followup.md` | Nudge / value / close / payment-reminder ke words |
| `config/design-seeds.json` | Palettes + fonts + layouts (aur uniqueness) |
| `config/campaigns/default.json` | Kaunse mohalle, kaunse niches, kitne leads/area |

Ek site ka fresh test:
```bash
curl -s -X POST http://127.0.0.1:8787/v1/demo/generate \
  -H "X-Gateway-Token: $(grep ^GATEWAY_TOKEN .env | cut -d= -f2)" -H 'Content-Type: application/json' \
  -d '{"business_name":"Test Cafe","category":"restaurant","city":"Karachi","area":"DHA","country":"PK","phone":"923001234567"}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);open("/tmp/t.html","w").write(d["html"]);print(d["design"],d["ok"],d["bytes"])'
xdg-open /tmp/t.html
```
Do baar chalayein → design **alag** aana chahiye (seed alag din/alag lead = alag). Yahi "har baar
unique" ka waada poora karta hai, model ke mood par chhodne ke bajaye.

Funnel analysis + suggestions: `make tune` (CRM data padh kar batata hai ke wording theek hai ya nahi).

---

## 12. Calling (phone) — imandari se

Poora module: `calling/README.md`. Khulasa: `docs/free-vs-paid.md`.

**Free phone calling is system me possible nahi hai**, aur jo log kehte hain ke hai woh aadha sach
kehte hain:

- AI *brain* free hai: Ollama/Gemini (LLM) + faster-whisper (STT) + Piper/Edge (TTS) — sab OSS, sab $0.
- Lekin **call kisi mobile number par karna = PSTN**, jiske liye ek carrier chahiye:
  | Chiz | Kya lagega |
  |---|---|
  | Twilio/Telnyx/Vonage number rent | ~$1–7/month |
  | Outbound minutes (India/Pakistan) | ~$0.03–0.09/min → 25 calls × 2 min ≈ **$1.5–4.5/roz** |
  | Pakistan me inbound mobile number | practically available nahi (PTA-regulated, foreign carriers ko nahi milta) |
  | TCPA/DND (US) cold-call rules | fine risk — yahan calling bilkul avoid karein |

**Free alternative jo maine bana diya hai:** WhatsApp **voice note** — awaaz ka asar, zero cost,
usi thread me jahan demo link hai:
```bash
docker compose --profile voicenote up -d
bash scripts/voice-note.sh 923001234567 "Bismillah Bakers" "https://.../demo/"
```
Ya n8n ka `05-voice-note-optional` workflow (webhook `POST /webhook/voice-note`).
Phone **agent ka dimaagh** bhi bana diya hai (state machine + Roman-Urdu intent classifier +
objection handling) — `python3 calling/agent.py --selftest` aaj hi chalega, bina internet ke.
Asli phone audio jodna chahein to `calling/README.md` + `docs/free-vs-paid.md`.

**Meri salaah:** 3 mahine sirf WhatsApp + email. Jab 10 paying customers ho jaayein, tab $50/month
rakh kar calling layer add karein — us waqt aapke paas data hoga ke kaun reply karta hai.

---

## 12b. Docs — kab kaunsi file padhni hai

| Situation | File |
|---|---|
| "Isme paid kya hai?" (sabse pehle yeh) | `docs/free-vs-paid.md` |
| Number safe rakhna / limits | `docs/anti-ban.md` |
| Pehle 7 din roz kya karna hai | `docs/first-7-days.md` |
| Hosting (Pages/CF/local), custom domain | `docs/hosting.md` |
| Baserow vs Google Sheets, 44 fields, statuses | `docs/crm.md` |
| n8n import, node errors, env vars | `docs/n8n-import.md` |
| Kuch toot gaya | `docs/troubleshooting.md` |
| Public URL / tunnel options | `docs/tunnel.md` |
| Pricing, upsell, refund policy | `docs/packaging.md` |
| Consent, data, invoice, tax | `docs/compliance.md` |
| Google Places se behtar leads (paid) | `docs/leads-google.md` |
| Phone calling / voice note | `calling/README.md` |

## 13. Commands cheat-sheet

```bash
make help          # sab targets
make status        # kaun chal raha hai, kaun nahi (sabse zyada use hoga)
make dryrun        # poora pipeline test, kuch send nahi
make live-test     # generated message khud ke number pe
make report        # funnel numbers (terminal)
make tune          # data padh kar suggestions
make logs          # n8n + gateway logs
make waha          # WhatsApp QR dobara
make tunnel        # public URL dobara
make crm           # Baserow table dobara banayein
make reset         # ⚠️ CRM ke rows delete
make test          # gateway ke unit tests (35) — CI jaisa
make validate      # workflow JSON ki shape check
make build         # prompts/tools badalne ke baad workflow JSON regenerate
```

Backup: `tar czf backup-$(date +%F).tgz .env n8n baserow gateway/data config prompts` — bas yehi
sab hai aapka business. Recovery: file wapas, `docker compose up -d`.

---

## 14. Architecture (2 minute me samajh lein, phir kabhi nahi atkega)

- **n8n = traffic cop.** Sirf triggers, HTTP calls, branches. Jaan-boojh ke "patla": n8n ke JSON me
  bhaari logic daalne se workflows toot-te hain aur debug namumkin ho jaata hai.
- **ai-gateway (`gateway/`) = dimaagh.** Free-tier survival: Gemini→Groq→Ollama fallback, RPD budget,
  Overpass mirror retries, Nominatim cache + 1 req/s policy, HTML extract + repair pass,
  liveness-checked deploy. Testable: `make test`.
- **Prompts alag files hain** (`prompts/*.md`) — runtime par padhi jaati hain. Edit → save → next run
  me naya prompt. Rebuild nahi, restart nahi.
- **CRM = source of truth.** Har lead ka status, thread count, demo URL. Baserow default
  (self-hosted, no OAuth). Sheets chahiye to `docs/crm.md`.
- **Deploy verification:** link bhejne se pehle gateway us URL ko 200 tak poll karta hai. 404 demo
  link = ek lost customer; isliye yeh non-negotiable hai.

`tools/build_workflows.py` se JSON banta hai + `tools/validate_workflows.py` check karta hai —
isliye aapko kabhi n8n ka JSON haath se nahi badalna (badalna bhi chahein to edit karein, chalega).

---

## 15. Troubleshooting (short list — poori `docs/troubleshooting.md` me)

| Lakshan | Kaaran + hal |
|---|---|
| `all AI providers failed [gemini:429]` | Free tier RPM/TPM hit. `make status` me `today_left` dekhein. Hal: `AI_DAILY_REQUESTS_PER_PROVIDER` kam, `MAX_LEADS_PER_DAY` 20→10, ya Groq key add karein |
| Site HTML `too small / truncated` | Model ne output limit tod di. `AI_MAX_TOKENS=16384` (Ollama pe 4096 default hota hai — barhayein) |
| Demo link 404 | Pages abhi build kar raha (`make pages`). `.nojekyll` commit karein |
| Replies nahi aa rahe | `make status` → webhook `000`? tunnel mar gaya → `make tunnel`. WAHA hook me URL sahi? |
| Dobara-dobara reply | 03 me **ACK 200** node webhook ke turant baad hona chahiye, end me nahi |
| 2 message bhej diye | WAHA me 2 session? `docker compose restart waha` ek baar; phir `status` check |
| Number restrict ho gaya | 30/day se zyada, ya naye number se cold blast. `docs/anti-ban.md` padhein |
| `BASEROW_TABLE_ID` missing | `make crm` chalayein, phir `docker compose up -d n8n` |
| AI English me jawab de raha hai | Lead ka `country` PK/BD/IN nahi → `PRICE_MAP`/language auto hai; force: `LANGUAGE=roman-urdu` |

---

## 16. Kya *nahi* bana (taake aapko surprise na mile)

- ❌ Payment auto-detect (Easypaisa/JazzCash ka koi free API nahi). Aap khud confirm karte hain —
  `paid=yes` ek click. Chahein to screenshots AI padh leta hai (`03` me `screenshot_received` flag ready hai).
- ❌ Automatic GST/invoice/legal paperwork.
- ❌ Real phone dialing (upar padha).
- ❌ Cloudflare Pages ka *auto*-domain setup (token daalein, baaki code me hai).
- ❌ Facebook/Instagram DM outreach (ToS + no free API) — WhatsApp + email se zyada chalta bhi hai.
- ⚠️ OSM data Pakistan me ~85-90% complete hai: nayi dukaanein miss ho sakti hain. Google Places
  lagana chahein to `docs/free-vs-paid.md` me tareeqa hai (card chahiye, 10k calls/month free).

---

**Chalne ke baad:** `make report` ka pehla screenshot sambhaal lein. 100 leads → ~15 reply →
2-4 paying customer, yeh realistic range hai (cold WhatsApp outreach; koi "90% conversion" wala
jhoot nahi). 2 hafta wording test karein, phir scale par jaayein.
