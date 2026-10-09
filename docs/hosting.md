# Hosting: demo sites kahan live hongi (teeno free options)

System ka sabse nazuk hissa: **link customer ko khulna chahiye.** Isliye gateway deploy ke baad
URL ko poll karta hai jab tak 200 na aa jaye (`_wait_live`, max ~2 min), phib hi message queue
hoti hai.

---

## A) GitHub Pages — default, sabse kam kaam

```
PAGES_BASE_URL/<slug>/index.html   →   https://user.github.io/demo-sites/bismillah-dha-1a2b/
```

**Kyun yeh default hai:** deploy = ek `PUT /contents/<path>` API call. Koi CLI nahi, koi build
config nahi, koi git clone nahi. Fine-grained PAT with `Contents: write` = bas.

**Do chhoti cheezein jo log bhoolte hain:**
1. Pages source **main / (root)** select hona chahiye (Settings → Pages).
2. `.nojekyll` commit karein — warna Jekyll har deploy pe 60-90 s leta hai aur files ko process
   karne ki koshish karta hai. `make pages` check kar leta hai.

**Limits:** 1 GB per site, 100 GB/month bandwidth, soft cap 100 sites/repo ke around comfortable
rahenge. Aapke 200-300 demos ek repo me theek hain (single-file, ~30 KB each = 9 MB total).

**Custom domain (recommended jab aap paid ho):** `demo.aapkadomain.com` par Pages ka CNAME →
GitHub repo me `CNAME` file + `Enforce HTTPS`. Client ko `username.github.io` se zyada
`demo.webdemo.pk` trust karta hai — replies ~2x tak barh sakte hain. Free hai (DNS ke ilawa,
domain ka kharcha ~$10/saal — ya `.pk` ~$30 — optional).

**Client ko handover:** site client ke apne repo me move karna = 2 min:
repo → transfer (settings) ya `git clone` karke unke repo me push, aur `.env` ka
`GITHUB_REPO` naya kar dein. Purane links toot-te hain → CRM ke `demo_url` refresh karein
(`scripts/report.sh` me list mil jaati hai). `PAGES_BASE_URL` update → naye links naye repo pe.

---

## B) Cloudflare Pages — instant deploy (build wait nahi)

System me `DEPLOY_TARGET=cf-pages` ready hai (direct Upload API, `gateway/deploy.py`).

```bash
# 1. Cloudflare account (free) → Pages → create project "demo-sites" → Framework: None
# 2. API token: My Profile → API Tokens → Create → template "Edit Cloudflare Workers"
#    (Pages:Edit chahiye) + account include
# 3. .env
DEPLOY_TARGET=cf-pages
CF_ACCOUNT_ID=            # URL bar me project khulte waqt dikhta hai
CF_PAGES_PROJECT=demo-sites
CF_API_TOKEN=
PAGES_BASE_URL=https://demo-sites.pages.dev
```
Link: `https://demo-sites.pages.dev/<slug>/`. Production alias ke liye custom domain add karein
(Pages → Custom domains) — `PAGES_BASE_URL` ko us par set kar dein.

**Fayda:** deploy turant, unlimited bandwidth, built-in HTTPS, 500 files/project se zyada par bhi
chalta hai. **Nuqsan:** token scope samajhna parta hai; `manifest` upload ka format strict hai
(isliye `make dryrun` se ek baar zaroor test karein).

---

## C) Local preview — bina kisi account ke testing

```bash
DEPLOY_TARGET=local
```
Gateway `/data/public/<slug>/index.html` likhta hai aur `/preview/<slug>/` par serve karta hai.
Zero setup, `make dryrun` isi pe chalta hai (public URL chahiye to `PUBLIC_PREVIEW_URL` set karein
— tunnel ka URL kaafi hai).

**Customers ko yeh link NAHI bhejein** (container restart pe files rehti hain magar server aapke
ghar pe hai). Sirf testing ke liye.

---

## Domain/branding ka sach (conversion par asar)

| Link shape | Owner ka reaction |
|---|---|
| `user.github.io/demo-sites/abc/` | "free lag raha hai" — chalega, kam trust |
| `demo.aapkadomain.com/abc/` | "ye banda serious hai" — **recommend yehi** |
| `192.168.1.5:8787/preview/abc/` | "kya bakwas hai" — kabhi na bhejein |

Ek `.com` (~$10/saal) ya `.pk` is system ka sabse zyada ROI wala kharcha hai. Free rakhna hai to
GitHub Pages hi rakhein — zindagi chalti hai us se.

## Ek aur cheez jo aapko karni chahiye (2 min, free)
`config/design-seeds.json` ke palettes me se 2 hata dein jo aapko pasand nahi. Aap product bech
rahe hain — apna taste encode karein, random ko na chhodein.
