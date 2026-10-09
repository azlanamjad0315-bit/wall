# Chhota legal/compliance page (main engineer ki salaah nahi — lawyer ki baat hai)

Aap businesses ko bech rahe hain, individuals ko nahi. Isse aapka risk chhota hai, magar zero
nahi. Yeh 6 cheezein likh lein, baaki fikr chhod dein.

## 1. Data aapke paas kya-kya hai
Lead name + phone + address + category (public OSM/business data). Yeh **personal data** hai
(Pakistan: PECA 2016 + provinces ke data rules; EU: GDPR; India: DPDP Act 2023).
- ✅ Theek: business ki publicly listed phone par business ke baare me message.
- ⚠️ Ghatiya: owner ka **personal** number, ghar ka pata, LinkedIn se scrape kiye numbers,
  data bechna/ share karna.
- ❌ Absolute no: unka business ka camera/social se nikala hua personal content, customer lists.

## 2. Marketing consent
- **Pakistan/India/Bangladesh:** B2B WhatsApp cold message practically tolerated hai, magar
  "not interested" = **hamesha ke liye band** (system `not_interested` par follow-up rok deta hai —
  yehi compliance hai). DNC registry (India TRAI) par B2B strictly nahi lagta, lekin complaint
  aane par operator action leta hai.
- **EU/UK/Canada/Australia:** cold **email** marketing generally permitted with unsubscribe +
  identity (PECR/CASL); cold WhatsApp to individuals = risky. **US:** TCPA — phone/SMS marketing
  ko consent chahiye; business landline calling alag rules, magar AI voice = FCC 2024/2025
  ruling ke under **robocall** = $500-1,500 per call. Isliye `campaigns/default.json` me
  London/NYC/Dubai `enabled: false` hai. Unhe tabhi on karein jab aapke paas **inbound** opt-in ho.

## 3. Jo ek line har message ke neeche honi chahiye
```
Agar aap ye messages nahi chahte to "STOP" likh dein — foran band.
```
`OFFER_TEXT` me daal dein. Isse (a) compliance line satisfy hoti hai, (b) "STOP" reply `03` me
`not_interested` ban jaata hai, (c) client ko respect milta hai. 5 words = sabse sasti insurance.

## 4. Demo site par kya likha hai (aapka footer) — yeh zaroori hai
Generated site me 2 cheezein already inject hoti hain:
- **"DEMO — not live yet"** badge (`DEMO_BADGE`),
- aapki agency line + WhatsApp link (`AGENCY_NAME`, `CONTACT_PHONE`).
Inhein hatana *mana* hai (gateway enforce karta hai). Wajah: client ko dhoka dene se behtar hai
ke aap saaf-saaf "maine aapke liye prototype banaya" bolein. Yeh aapko "who made this?" ke fraud
allegation se bachata hai, aur 24% log exactly is line ki wajah se reply karte hain.

## 5. Contract/invoice (bina lawyer ke 90% kaam)
WhatsApp par close karte waqt yeh 5 lines bhej dein (copy-paste ready):
```
1) Scope: 1-page site: hero, menu/gallery (aapka content), timings, WhatsApp button, Google Maps link.
2) Delivery: content milne ke 24 ghante me live.
3) Revisions: 2 free (7 din me), phir 3,000/round.
4) Hosting: 1 saal included (aapke account par), phir 4,000/saal.
5) Guarantee: pasand na aaye to pura paisa wapas, site hata dunga.
Payment: <Easypaisa/JazzCash> — site isi din deploy.
```
Stamp/letterhead nahi chahiye. WhatsApp pe "agree" + payment screenshot = Pakistan courts me
kaam karti hai (Electronic Evidence Act + PECA). Ek PDF template bana lein, har deal me paste.

## 6. AI-generated content ka disclosure
Aap AI se site bana rahe hain. Client ne pucha to jhoot na bolein. Behtar hai pehle hi bolein:
> "Design + copy AI se draft hota hai, main haath se theek karta hoon — isliye 15,000 me ho raha hai,
> 150,000 me nahi." Yeh aapki *price justification* hai, kamzori nahi.
AI se bana copy kabhi copyright-claim-free nahi hota — isliye client ke photos/text use karwayein,
aur stock text (lorem, fake reviews) **nahi** chhodein live site par. `site-generator.md` isi liye
"placeholder copy = bug" kehta hai.

## 7. Tax/registration (Pakistan-specific, practical)
- Sole proprietor + FNT/ NTN (FBR portal, free) — 15 din me ho jaata hai, business account aur
  Easypaisa **merchant** ke liye chahiye.
- Sales tax on digital services: current threshold/registration FBR se confirm karein (badalta hai);
  15,000/ticket par threshold ke neeche rehna aam baat hai.
- Easypaisa/JazzCash **personal** account se 50+ transactions/month par account limit/review aata hai.
  **Merchant account** lein (PKR 0-2,000 setup) — yeh aapka paisa bachayega, aur client ko receipt
  dene se trust barhta hai.
> Yeh legal advice nahi, operational reality hai. Deal size barhe to ek accountant se 1 ghanta
> baat kar lein — PKR 3,000 kharcha, PKR 300,000 ki musibat bachegi.
