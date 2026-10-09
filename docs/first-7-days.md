# Pehle 7 din — checklist (din 1 ka setup README me hai; yahan roz ka kaam)

Yeh file print kar lein. Iske ilawa aapko koi "course" nahi chahiye.

## Pehle 7 din: 10 message/roz, sirf 4 areas, WhatsApp
**Din 1 — setup complete + sirf khud ko**
- [ ] `make status` sab green (koi `MISSING` nahi)
- [ ] `make dryrun` → site mobile par khuli, link `live: true`
- [ ] `make live-test` → message **aapke** number par aaya, wording pasand aayi
- [ ] n8n me 4 workflows active, `02` ka cap `MAX_OUTREACH_PER_DAY=5` (pen! foran badalna nahi)
- [ ] Telegram par `make test`-type manual run ka notification aaya

**Din 2 — pehli 5 real leads (apne mohalle se)**
- [ ] `bash scripts/dryrun.sh --area "Aapka Area" --limit 5 --to <apna number>` se
      pehle 5 messages *preview* karein
- [ ] pasand aaye to `01` + `02` chhod dein; raat ko `bash scripts/report.sh`
- [ ] har `not_interested` ko CRM me haath se 1 note daalein ("mehngi", "already hai", "khaali") —
      yeh 30 second ka kaam `make tune` ko agle hafte aapki wording fix karwayega

**Din 3 — replies ka system check**
- [ ] kisi dost ke number se fake "interested" bhejwayein → Telegram alert + AI ka 2-line reply
      aaya? Aaya to system zinda hai.
- [ ] 5 minute me aapne manually follow-up kiya? Kiya — kyunki ** asli paise yahan bante hain**
- [ ] `03` ke `Decide` node ka output dekhein: confidence/intent sane lag rahe hain?

**Din 4-5 — pehla pitch**
- [ ] jinhone reply kiya unhe call/WhatsApp voice note bhejein (2 minute, insaan ki tarah)
- [ ] haan kehne par: `docs/packaging.md` ki 5-line scope WhatsApp par bhej dein + payment info
- [ ] paisa aate hi CRM me `paid=yes` (2 click) — data hi aapki agli marketing hai

**Din 6 — data review (10 min)**
- [ ] `bash scripts/report.sh` → reply rate kitna? 0% ho to **message band, wording badlo**
- [ ] `prompts/outreach-roman-urdu.md` me ek line change karein (sirf ek — warna kuch nahi pata chalega)
- [ ] `make tune`

**Din 7 — cap barhao, baseline set karo**
- [ ] reply rate ≥8% aur koi Meta warning nahi → `MAX_OUTREACH_PER_DAY=10`
- [ ] areas: `config/campaigns/default.json` me ek naya mohalla add karein
- [ ] `tar czf ~/backup-$(date +%F).tgz .env n8n baserow config prompts` (weekly aadat)
- [ ] Excel/Sheets me ek line likhein: leads 35, replies 4, interested 2, **paid 1, PKR 15,000**

## Roz ke 5 minute (din 8 se hamesha)
| Waqt | Kaam | Kitna |
|---|---|---|
| Subah chai | `01` ke baad report dekhein: naye leads, kaun fail hua | 90 s |
| Dopahar | Telegram 🔥 = **aap** baat aage barhayein | 3-5 min |
| Raat | 21:00 report — follow-up list kal ke liye ready | 60 s |
| Jumeraat | `make tune` + `make pages` + backup | 10 min |

## Jab 3 cheezein ho jaayein, tab scaling
1. **≥3 paying customers** → `docs/tunnel.md` (option B: apna domain + named tunnel) +
   `docs/hosting.md` (custom domain) + Easypaisa merchant account
2. **≥8 paying** → cap 15/day doosre SIM + doosre area ke saath (2 numbers = 2x volume,
   1 number = aapka sab kuch ek jagah)
3. **≥20 paying** → pricing 25,000 karein + `docs/packaging.md` ka Growth tier; tab aapko
   automation se zyada **delivery capacity** chahiye

## Khamoshi se chhupi hui baat
Is system ka sabse bara bottleneck AI nahi, WhatsApp nahi, hosting nahi — **aapka 5-minute
manual follow-up** hai. Jo log "full automation" chah kar reply ke baad 6 ghante late karte hain,
woh 3% convert karte hain. Jo 5 minute me haath se baat karte hain, woh 30%. Yeh software
aapka *lead gen* automate karta hai, *sales* nahi — aur yeh theek hai, kyuñke sales me paisa hai.
