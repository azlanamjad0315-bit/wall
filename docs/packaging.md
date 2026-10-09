# Pricing, packaging aur upsell (yeh system sirf leads nahi, business model hai)

Aap `PRICE_LABEL=PKR 15,000 one-time` par khade hain. Yeh number theek hai **agar** delivery
30 minute me ho. Neeche wala structure usi system se automatically chalaya ja sakta hai
(sirf `.env` + 3 prompt lines badal kar).

## Tiers (same pipeline, teen price points)
| Tier | Kya | Price (PKR) | Aapka waqt | Workflow support |
|---|---|---|---|---|
| **Starter** | 1-page demo site, live in 24 h, 1 month hosting included | 15,000 | 25 min | default |
| **Growth** | Starter + menu/gallery + Google Business profile fix + 3 months edits | 35,000 | 60 min | `EXTRA_SECTIONS` + follow-up 3 me offer |
| **Local Legend** | Growth + booking/WhatsApp order flow + 6 months + monthly report | 75,000 | 3 h | upsell script (neeche) |

Ek baat clear kar dein: **15,000 par aap demo bech rahe hain, "website business" nahi.** Client
ko chahiye to usko apna domain + edits + maintenance chahiye — woh tier 2/3 hai. Yeh distinction
aapko 3 mahine me 30,000 PKR se 300,000 PKR tak le jaata hai bina naya lead dhoone.

## Upsell — pipeline ke andar se (2 line change)
`prompts/followup.md` ke stage 3 me yeh line daal dein:
> "Agar aap chahein to menu + Google profile + 3 mahine ki edits ke saath **35,000** ka
> Growth package bhi laga sakta hoon — bolo to kal tak live."
Data ye batayega ke kitne `%` log tier-2 lete hain (`make tune` me `interested → paid` rate).

## Revision policy (aapki jaan bachayega)
```
2 revisions free (7 din ke andar). Uske baad PKR 3,000/round.
Content (photo, menu, timings) aap denge — 24 ghante me live.
Hosting: 1 saal included (aapka GitHub/CF account), uske baad PKR 4,000/saal
         ya client apna account le le (free for you, main recommend yehi 6 mahine baad).
```
Yeh text `OFFER_TEXT` me daal dein — jo har message ke neeche chala jaata hai, aur AI usko
quote karta hai jab price question aata hai. **Clear policy = 90% jhagde khatam.**

## Refund / guarantee line (trust + conversions)
> "Pasand na aaye to bolo, site hata deta hoon — 15,000 wapas, koi sawaal nahi."
Sunnne me risky lagta hai; data kehti hai ke SMB website deals me refund claim <3% hota hai,
aur guarantee reply-rate ko double kar sakti hai. Aapke paas physical delivery nahi hai, isliye
yeh guarantee sachchi hai. **Haan, ise OFFER_TEXT me daalein.**

## 100 leads ka realistic funnel (PK market, WhatsApp cold)
```
100 leads scanned (20/day → 5 din)
 → 88 delivered, 64 dekha (blue tick)
 → 8-12 replies  (8-12% — acha hai; 3% se kam ho to wording, 15%+ ho to aap lucky hain)
 → 4-6 interested → 2-3 pay  → PKR 30,000-45,000 / 5 din
```
Isko mat chhupaiye apne se: agar reply rate 3% hai to **naye leads ki zaroorat nahi, naye
words chahiye**. `make tune` isi liye hai. 4 hafte wording test karein, phir volume barhayein.

## Kab band karna/chhodna chahiye (honest tripwires)
- 3 mahine, 900 messages, 0 paying customer → offer theek nahi (price nahi, *offer*). Free
  1-page demo bana kar pehle 10 logon ko muft dein, testimonial ke badle. Phir bechein.
- Number 2 baar restrict hua → WhatsApp band, sirf email + in-person (walk-in with printed QR
  of demo). Aapka CAC tabhi wapas aata hai jab channel zinda ho.
- 40+ active clients ho gaya → maintenance reality check: 40 sites × 1 edit/mahina = 40 edits =
  aapka poora din. **Maintenance plan se pehle pricing badhayein.**
