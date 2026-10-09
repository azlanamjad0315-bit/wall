# Free vs paid — poora hisaab (koi chhupi hoi laagat nahi)

Aapke rules ke mutabiq: sab kuch free. Yeh uski sachchi list hai — kya free hai, kis ki
limit hai, aur kya kabhi free nahi ho sakta.

## 100% free, koi card nahi, koi sign-up nahi

| Kaam | Tool | Limit (sach) |
|---|---|---|
| Workflow engine | n8n (self-hosted, Docker, fair-code) | Unlimited local; `execute` par koi fee nahi |
| Lead data | OpenStreetMap Overpass + Nominatim | No key. Policy: 1 req/s Nominatim + contact email, Overpass ~10k queries/day soft-cap |
| AI (site+messages) | Gemini API free tier | ~10-15 RPM, ~500-1500 req/day (model par depend). Data Google training me ja sakta hai (AI Studio me opt-out) |
| AI alt | Groq free tier | ~30 RPM, ~200K tokens/day per model. **Free model list rotate hoti rehti hai** — isliye model name `.env` me hai, code me nahi |
| AI local | Ollama (qwen2.5 / llama3.1) | $0, private. Halke model se HTML kamzor banta hai; 7B ke liye 8GB RAM, 14B ke liye 16GB |
| Hosting | GitHub Pages | 1 GB site, 100 GB/month bandwidth — aapke liye kaafi (hazaar demos bhi). Build 20-60 s |
| Hosting alt | Cloudflare Pages | Unlimited bandwidth free, instant deploy, custom domain free |
| CRM | Baserow (self-hosted) | Unlimited rows, apna data. Ya Google Sheets (free, 10M cells) |
| WhatsApp | WAHA Community | Free. **Risk aapke number par hai** (docs/anti-ban.md) |
| Email | Gmail SMTP + app password | Free. ~500/day soft limit; naye account pe kam |
| Alerts | Telegram Bot API | Free, 30 msg/s |
| TTS (voice note) | Edge-TTS / gTTS / Piper | Free, no key. Urdu quality Piper me kamzor, Edge me achi |
| Public webhook | Cloudflare quick tunnel | Free, no account. URL badalta hai; named tunnel bhi free (domain chahiye) |

## Free nahi hai (aur koshish se bhi free nahi hoga)

### 1. Phone calling (PSTN) — sabse bara jhoot jo log bechte hain
AI brain free hai. **Lekin dial karna nahi.**
- Number rent (Twilio/Telnyx/Vonage/Plivo): **~$1–7/month**
- Outbound minutes: India ~$0.006–0.015, Pakistan ~$0.03–0.09, UK ~$0.015, US ~$0.008
  → 25 calls × 2 min Pakistan = **~$1.5–4.5/roz** = $45–135/month. Aapka ek customer = 15,000 PKR
  ≈ $54. Yani calling se pehle 1-2 customer se zyada kuch nahi bachta.
- **Pakistan me inbound mobile number** foreign carriers ko practically nahi milta (PTA regulation,
  local reseller chahiye, ISDN/E1 setup = lakhon rupaye).
- US/Canada: TCPA + DND registry — cold-call AI dialer = **$500–1,500 per violation** risk.
- India: TRAI DND + commercial calling rules; unregistered telemarketer = penalty.

**Free substitute (maine bana diya):** WhatsApp voice note (`scripts/voice-note.sh`,
`n8n/workflows/05-voice-note-optional.json`). Awaaz ka asar, 0 paisa, usi thread me.

**Doosra free substitute:** LiveKit **WebRTC** call = dono taraf app/browser ho to free
(self-hosted LiveKit server). Lekin aapka prospect SMS/WhatsApp par hai, WebRTC me nahi.
Isliye practical nahi.

### 2. Google Maps Platform (Places API) — "free" jaisa dikhta hai, nahi hai
- 2025 ke baad billing profile lazmi. Free quota: **10,000 Essentials calls/month**, uske baad
  ~$17/1000 calls Places API par.
- Advantage OSM par: reviews count, phone accuracy, "has website" data behtar; `businessStatus:
  OPERATIONAL` se band dukaanein filter hoti hain.
- Chahein to: `docs/leads-google.md` ka plan (gateway me 1 endpoint add karna = 30 line code).
  Main default OSM par isliye hoon kyunki aapne kaha *koi paid service nahi* — Google ka "free tier"
  technically paid product hai jisme card mangta hai, aur limit cross hone par chup-chaap bill aata hai.

### 3. WhatsApp official Business API (Meta)
- Cloud API: free tier 1,000 service conversations/month, uske baad per-message (marketing
  ~$0.05 PK). Lekin **template approval** + business verification = 3-7 din aur Meta ki
  marketing restrictions. Cold outreach templates approve hi nahi hote.
- Isliye WAHA (unofficial) = free, lekin ToS risk aapka. Trade-off samajh kar choose karein:
  official API = paisa + compliance; WAHA = $0 + aapki zimmedari.

### 4. Email deliverability tools
- Free: Gmail SMTP. Lekin naye Gmail account se bulk = spam folder.
- Hal (bina paisay): domain + SPF/DKIM/DMARC **agar** domain already ho (DNS free); warna
  25-30 email/day rakhein, plain text, zero links pehle message me (aapke paas 1 link hai —
  woh demo ka — acceptable), aur warmup: pehle 2 hafte 10/day.

### 5. Cheezein jo maine jaan-boojh kar *nahi* banayin
- Payment auto-verify (Easypaisa/JazzCash koi free public API nahi deta). Workaround: business
  screenshot bhejta hai → 03 workflow `screenshot_received` flag laga deta hai → aap confirm.
- Auto-invoice/GST.
- Facebook/Instagram DM scraping (ToS violation + ban = aapka main account khatra).
- "1000 messages/day" mode: 25 se upar jaana number kho dene ke barabar hai.

## Asli monthly cost (poora sach)
```
$0  ------------------ aaj ka setup (self-hosted, free tiers)
$5-7 ------ chahein to ek domain (agents.pk jaisa) — trust +20%, replies +
$5-10 ----- chhota VPS agar ghar ka laptop 24/7 nahi chala sakte
$0  ------------------ calling (kyunki hum voice note bhejte hain)
```
Domain + VPS lena behtar hai agar aap **serious** hain — lekin zaroori nahi, isliye default nahi.
