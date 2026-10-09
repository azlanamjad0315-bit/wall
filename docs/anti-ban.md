# WhatsApp + email outreach: zinda rehne ka manual

⚠️ **Pehli shart:** yeh doc padhna optional nahi hai. WAHA unofficial API hai — Meta ke terms
ke against hai. Aap jo risk le rahe hain woh **aapka number** hai. System ki poori design isi
klime par bani hai: kam message, der se, insaan jaisa, aur har reply ke baad ruk jaana.

## Number ki taiyari (din 0)
1. **Spare SIM** lein (ya purana number). Personal/main business number kabhi nahi.
2. Us par WhatsApp **Business** install karein, profile photo + description + address daalein.
   Khaali profile = bot signal.
3. 3-5 din tak **normal** use karein: family ko messages, 2-3 groups me replies, ek status.
   Naye number par pehla din hi outreach karna sabse tez ban hone ka tareeqa hai.
4. Device WAHA ke saath link: `bash scripts/waha-login.sh`. Ek device hi linked rakhein — phone
   par bhi chalte rahein to session drop hota hai.

## Rate ladder (yehi asli growth hack hai)
| Din | `MAX_OUTREACH_PER_DAY` | `OUTREACH_MIN..MAX_DELAY` | Note |
|---|---|---|---|
| 1–7 | 10 | 180–400 s | sirf replies par focus, tracking off |
| 8–14 | 15 | 140–320 | agar koi warning nahi aayi |
| 15–30 | 20–25 | 100–280 | 25 = meri hard ceiling |
| 30+ | 25–30 | 90–280 | **30 se upar mat le jaayein.** Neeche "kyun" hai |

`scripts/tune.sh` rate-limit ki salah nahi dega jab tak reply rate theek na ho — pehle quality, phir volume.

## System me kya-kya laga hai (aur kyun)
- **Random delay** har message ke beech (default 90–280 s). Fixed interval = bot signature.
- **Send windows** (`OUTREACH_CRON=0 11,17 * * 1-6`): roz 2 bache hue windows, raat ko kuch nahi.
  2 baje raat ko message = report = ban.
- **Typing indicator** (`/api/startTyping`) + 3–7 s ka pause before send.
- **`sendSeen`** par inbound message — double tick dikhta hai, insaan lagta hai.
- **`reply_to`** — AI ka jawab uske message par quote hota hai (thread behaviour).
- **Daily cap** (`MAX_OUTREACH_PER_DAY`) — bheje hue count CRM se gine jaate hain, isliye
  workflow dobara chalane par double nahi hota.
- **`LEADS_COOLDOWN_DAYS=30`** — ek lead ko mahine me 2 se zyada baar contact nahi.
- **Auto-reply cap** (`AUTO_REPLY_MAX_PER_THREAD=2`) — AI khamosh ho jaata hai, baat aap sambhaalte
  hain. 2 se zyada auto-reply = "bot" ka sabse bara signal.
- **No link-first rule nahi, balki 1-link rule** — ek hi link (aapka demo). Multiple links = spam pattern.
- **Fallback text** jab model rules tode (banned phrase, price leak, 2 emoji) — safety gate
  `02-outreach` me. Yeh gate hi asal insurance hai.

## Red flags jo aapko khud dekhne hain (system nahi dekh sakta)
- Meta ka in-app warning: "your account may be restricted" → **foran 3 din sab band**, phir 5/day se
  shuru karein.
- Messages "sent" (1 tick) par atak jaayein 1 ghante se zyada → number flagged. Ek din break.
- Ek hi message par 2+ users ne "Report"/"Block" kiya → copy change karein (report `04` me
  `send_failed`/`not_interested` count se pata chalta hai).
- Naya number + 30 cold messages/day with a link = 2-3 hafte me ban. Yeh statistics hai, meri
  ray nahi.

## Content rules (jo `prompts/outreach-*.md` enforce karte hain)
1. Pehle message me **price nahi**.
2. Ek se zyada CTA nahi. "Reply karein ya call karein" = zero action.
3. Jhoot nahi: fake referral, "maine 3 baar call kiya", nakli review count.
4. Personalisation kam se kam 2 cheezein: mohalla + category-specific kami.
5. "No" ka matlab "no" — `not_interested` par AI sirf file karta hai, follow-up nahi.
6. Business ka asli naam pehli line me — yehi scam-shak todta hai.

## Email side (GSMTP) — chhota sa section, kyunke woh bhi ban ho sakta hai
- Gmail ka naya account = 30-50/day bhi zyada hai. 2 hafte warm karein.
- Subject me "?"/"FREE"/caps nahi. Plain text, 1 link, no image, no attachment pehle touch me.
- **Bounce** aate hi us address ko hataayein (CRM `send_failed`) — 3%+ bounce = spam filter trigger.
- "Unsubscribe" ka ek line optional hai; Pakistani SMB outreach me zaroori nahi, lekin
  `not_interested` par hamesha ruk jaayein.

## Backup plan (agar number phir bhi gaya)
1. `docker compose stop waha`, `mv waha waha-old` → naya session, naya SIM, scan.
2. CRM safe hai (Baserow/Sheets) — kaam wahin se continue hota hai jahan se raha.
3. Outreach cadence aadha karein aur 2 hafte tak sirf **inbound** replies handle karein.
4. Iske baad serious scale karna ho: Meta Cloud API (paid, templates approved) ya email-only.
   `docs/crm.md` me Sheets-only ka option hai; 02 ka WhatsApp branch disabled kar dena kaafi hai.

## Ek honest line
25 cold WhatsApp messages/roz ek banda hai. 500/roz ek spammer hai jo 3 hafte me gayab ho jaata hai.
Is system ki value "zyada message" nahi, "har message ke baad aapka naam zinda rehna" hai.
