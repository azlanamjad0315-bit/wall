# Calling module (optional) — poora sach + jo bana hai woh

Aapne kaha tha: *"Calling ka optional module banao aur honestly batao ke paid kya hoga. Pehle
WhatsApp/email par focus karo."* Yeh usi ka answer hai, teen hisson me.

---

## 1. Jo aaj hi **$0** me chalta hai ✅

### a) Voice note — phone ka asar, WhatsApp ki delivery
`voicenote.py` ek free TTS sidecar hai (FastAPI, compose profile `voicenote`). Backend ladder:
**Piper** (100% offline neural) → **Edge-TTS** (Microsoft ka free endpoint, `ur-PK`/`hi-IN` voices
available) → **gTTS** (Google Translate, `ur` — thoda robotic, magar hamesha chalta hai).
Koi API key, koi account, koi card nahi.

```bash
docker compose --profile voicenote up -d
curl -s http://127.0.0.1:8090/healthz | python3 -m json.tool
# audio banao (file /media/vn-*.ogg me milegi):
curl -s -X POST http://127.0.0.1:8090/tts -H 'Content-Type: application/json' \
  -d '{"text":"Bismillah Bakers, main Ali WebDemo se bol raha hoon. Aapke liye menu wala page bana diya hai. Link WhatsApp par bhej diya hai, ek baar dekh lein."}'
```
Language auto-detect hoti hai (`looks_urdu()`) — Roman Urdu ya Urdu script → `ur`, warna `en`.

WhatsApp par bhejne ke 2 tareeqe:
```bash
# (i) manually: AI script + TTS + WAHA sendVoice, ek command me
bash scripts/voice-note.sh 923001234567 "Bismillah Bakers" "https://u.github.io/demo-sites/bismillah/"
# (ii) n8n se: workflow 05 import karein → POST /webhook/voice-note
curl -s -X POST http://localhost:5678/webhook/voice-note -H 'Content-Type: application/json' \
  -d '{"phone":"923001234567","business_name":"Bismillah Bakers","demo_url":"https://u.github.io/demo-sites/bismillah/"}'
```
**Rule jo prompt enforce karta hai:** voice note me URL **bolta nahi** — "link bhej diya" kehta hai.
Wajah: 40-second ki awaaz me koi URL type nahi karta. Text message me link hota hai, awaaz trust
banati hai. Yeh combination cold outreach me sabse zyada chalta hai.

### b) Call ka **dimaagh** — ready, offline, tested
```bash
python3 calling/agent.py --selftest          # 8 intent cases + state machine (no network, no key)
python3 calling/agent.py                     # text REPL: owner ban kar type karein
python3 calling/agent.py --no-llm            # sirf scripted brain (zero API)
python3 calling/pipecat_call.py --text      # mic → faster-whisper → brain → TTS speaker
```
`agent.py` me jo cheez AI se zyada qeemti hai woh **state machine** hai:
```
greet → hook → handle(objection) → pitch → close → end
         │            │
         ├─ price → "15,000 one-time, hosting free 1 saal, 24h me live" → close
         ├─ trust → "jhooth nahi bolunga, link khol kar apna naam dekhein" (retry ≤ 2)
         ├─ busy  → "2 min me link bhej deta hoon"
         └─ no×2  → polite goodbye, FOREVER (CRM me not_interested)
```
Safety built-in: **hostile** ("fraud/thug/police") → pehla branch, koi pitch nahi, call off.
2 baar "nahi" → `said_no=True`, koi "last chance" nahi. Yehi woh cheez hai jo AI sales agents ko
customer banati hai.

### c) Browser/WebRTC call (dono taraf app ho) — technically free
Self-hosted **LiveKit** server free hai; `livekit-client` wale do browsers aapas me $0 me baat kar
sakte hain. Magar aapka prospect WhatsApp par baitha hai, LiveKit room me nahi. **Isliye practical
nahi.** (Yeh sirf isliye likha hai taake aapko na lage maine chhupaya.)

---

## 2. Jo **paid** hai — number aur minutes ❌

Free OSS se aap *call nahi* kar sakte, kyunki PSTN (public phone network) tak pohanchne ke liye ek
carrier ka darwaza chahiye. Woh darwaza bikta hai.

| Chiz | Realistic cost | Note |
|---|---|---|
| Twilio Pakistan outbound number + minutes | **$0.136/min** (Pakistan mobile) — numbers Pakistan me outbound-only | 25 × 3 min/roz = $10/roz = ~PKR 2,900 |
| Telnyx / Vonage / Plivo Pakistan | $0.02–0.09/min | rate theek, lekin **PSTN access Pakistan me whitelist-based** |
| Local SIP trunk (PTA-licensed operator, e.g. Nayatel/WTCC SIP) | PKR 3,000–15,000/mo + per-minute | legal, quality achi, setup 2-6 hafte, KYC |
| Incoming toll-free / DIDs for Pakistan | practically **nahi milta** international providers se | local operator se hi hoga |
| LiveKit Cloud (media) | free dev tier: 1000 unit-months? (badalta hai) → uske baad $0.02-0.04/min | media, PSTN nahi |
| ASR (STT) — **free** (local faster-whisper) | $0 | Deepgram/AssemblyAI = $0.006/min ka alternative |
| TTS — **free** (Piper/Edge/gTTS) | $0 | ElevenLabs = $5+/mo ka alternative |
| LLM — **free** (Gemini/Groq/Ollama) | $0 | paid = better objection handling |

**Calculation jo aapko karni hai:** PKR 15,000 ≈ $54 ek customer. Calling se **mahine me**
$300 (≈ 5,600 PKR) minutes ka bill banta hai 25 calls/roz × 3 min × 22 din par, aur cold-call
connect rate 3-8% hai, conversion 0.5-2%. Yani: calling layer aaj aapko **nusqan** de rahi hai.
30-40 paying customers ke baad yeh math palat-ta hai (jab aap $500+/customer bech rahe hon).

**Legal reality (aur yeh cost se zyada important hai):**
- **Pakistan:** commercial/telemarketing calling PTA rules + (soon-ish) DNC registry ke under aati hai.
  AI voice se automated calling = consent matter. Unknown number ko AI se call karwana aapko
  regulatory + reputational dono jagah phansa sakta hai.
- **India:** TRAI/DND — commercial calls unregistered telemarketer se = penalty + number snip.
- **US/Canada:** TCPA + 2025 ke baad **FCC ne AI-generated voice ko robocall law ke under explicitly
  define kiya hai** → **$500–1,500 per call** ka liability. Ek galti = 30 customer.
- **EU/UK:** GDPR + PECR — consent ke bina cold call = fine.

Isliye is repo me dialer **nahi** hai, aur main bana ke nahi de raha. Voice note free, legal, aur
(PKR 15,000 wale niche me) zyada effective hai.

---

## 3. Agar phir bhi calling karni ho — sahi raasta (jab aap ready hon)

`pipecat_call.py --sip` ka scaffold ready hai (faster-whisper STT → Ollama/Gemini → Piper TTS →
Twilio serializer). Usse chalane ke liye 4 cheezein:

1. **Carrier:** Twilio Elastic SIP Trunk (ya Telnyx/Vonage). Pakistan calling enable karne ke liye
   KYC + business proof lagega. 1 number rent karein.
2. **Consent + list hygiene:** sirf unhe call karein jinhone WhatsApp par *interest dikhaya*
   (status ∈ `interested`, `negotiating`, `revision_requested`). Cold dialling chhodein —
   **warm list** is system se automatically banti hai (`bash scripts/report.sh` ka
   `➜ FOLLOW UP` section). Yeh 3-5x behtar ROI hai, aur legal risk near-zero.
3. **Escape hatch:** har 3rd sentence par "main insaan se baat karwa deta hoon" → aapka number
   transfer. `agent.py` me `handoff` stage already hai.
4. **Recording + disclosure:** pehle 5 second me "yeh call record ho rahi hai" — AI voice
   disclosure **kai jurisdictions me lazmi** hai (California/UK EU AI Act transparency). `agent.py`
   ke greeting me ek line add kar dein; maine jaal-boojh ke nahi daali kyunki aapka jurisdiction
   mujhe nahi pata.

Estimated ongoing: **$30–80/month** (number + ~1,000 min + a paid LLM for quality). 30 customers
se zyada hone par hi justify hota hai.

---

## 4. Test checklist (aaj, 10 minute)
```bash
make test                                      # gateway ke 35 unit tests
python3 calling/agent.py --selftest            # 8 intent + state machine ✓
docker compose --profile voicenote up -d
curl -s http://127.0.0.1:8090/healthz           # "available": {piper/edge/gtts}
bash scripts/voice-note.sh <APNA_NUMBER> "Test Shop"      # phone pe voice note aaye ✓
python3 calling/pipecat_call.py --text          # mic mode (faster-whisper install ho to)
```
Jo cheez aap **dekh ke confirm** karein: awaaz natural hai? Roman Urdu theek bol raha hai?
Agar gTTS robotic lage → `TTS_BACKEND=edge` try karein (naye wale voices kaafi achhe hain).
Agar dono kamzor lagen → voice note skip karein; text + link behtar hai, warna aap awkward
awaaz bhej ke brand kharab karenge. Imandari se: Roman-Urdu TTS aaj bhi kamzor area hai.
