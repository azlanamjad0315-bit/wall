# Optional: Google Places se leads (free nahi — par behtar data)

Default **OpenStreetMap** hai: no key, no card, $0. Yeh doc un logon ke liye hai jab aap
mahine ke 100+ customers target kar rahe hon aur data quality khareedna chahte hon.

## Kya milta hai Google se (aur kyun OOSM nahi deta)
| Field | OSM (default) | Google Places |
|---|---|---|
| name, address, phone | ✅ (bharosemand) | ✅ |
| website | ✅ tags me, lekin missing/common | ✅ `websiteUri` |
| rating + review count | ⚠️ rare (Pakistan me ~5% POIs) | ✅ hamesha |
| `businessStatus` (permanently closed!) | ❌ | ✅ — **yehi sabse qeemti hai** |
| `currentOpeningHours` (open now) | partly | ✅ |
| photos (demo site me lagane ke liye) | ❌ | ✅ (attribution chahiye) |
| price level / service options | ❌ | ✅ |
| Cost | $0 | 10,000 Essentials calls/month free **ke baad** ~$17/1000 |

**Card mangta hai, isliye default nahi.** Free quota 10k/month aapke 30-50 leads/day ke liye kaafi
hai, magar Google Cloud console me billing account link karna padta hai — aur "excess" pe bill
aane ka risk hamesha rehta hai.

## Lagane ka sabse kam-khatari wala tareeqa (30 line code)
Google ko **enrichment** ke liye use karein, discovery ke liye nahi. Discovery (Nearby Search)
sasta hai lekin `businessStatus`/`websiteUri` details ke liye Place Details chahiye — dono ka
free quota alag count hota hai. Isliye: **pehle OSM se lead dhoondhein (muft), phir sirf unhi
~20 leads ke liye Google se 1 details call karein** = 20-600 calls/month = hamesha free rehna
guaranteed, agar billing cap bhi laga dein.

```python
# gateway/leads.py ke Radar class me ek method add karein (copy-paste ready):
GOOGLE_KEY = os.getenv("GOOGLE_PLACES_KEY", "")

async def enrich_google(self, lead: dict) -> dict:
    """1 Place Details call per lead. Never more than MAX_LEADS_PER_DAY/month*30."""
    if not GOOGLE_KEY:
        return lead
    from urllib.parse import quote
    q = quote(f"{lead['osm_name']} {lead.get('address','')} {lead.get('city','')}")
    async with httpx.AsyncClient(timeout=20) as cx:
        # Text Search se place_id (field mask chhota rakha = sasta)
        r = await cx.get("https://places.googleapis.com/v1/places:searchText",
                         headers={"X-Goog-Api-Key": GOOGLE_KEY,
                                  "X-Goog-FieldMask": "places.id",
                                  "Content-Type": "application/json"},
                         json={"textQuery": q, "maxResultCount": 1})
        pid = ((r.json().get("places") or [{}])[0]).get("id", "")
        if not pid:
            return lead
        r2 = await cx.get(f"https://places.googleapis.com/v1/places/{pid}",
                          headers={"X-Goog-Api-Key": GOOGLE_KEY,
                                   "X-Goog-FieldMask":
                                   "rating,userRatingCount,websiteUri,phoneNumber,businessStatus,"
                                   "currentOpeningHours,googleMapsUri,name,formattedAddress"})
        d = r2.json() or {}
    if d.get("businessStatus") in ("PERMANENTLY_CLOSED", "CLOSED_TEMPORARILY"):
        lead["skip"] = True                     # band dukaan ko site bechna = refund
    lead["rating"] = lead["rating"] or str(d.get("rating") or "")
    lead["reviews_count"] = lead["reviews_count"] or str(d.get("userRatingCount") or "")
    lead["website"] = lead["website"] or (d.get("websiteUri") or "")
    if d.get("websiteUri"):
        lead["hotness"] = max(0, lead["hotness"] - 25)   # website mil gayi = candidate nahi
    lead["maps_url"] = d.get("googleMapsUri") or ""
    return lead
```
Aur workflow 01 ke `Prepare leads` Code node me `if (l.skip) continue;` add kar dein.

`.env`: `GOOGLE_PLACES_KEY=...` (koi compose change nahi chahiye — `AI_EXTRA_PROVIDERS` jaisa
passthrough `environment:` block me line daal kar reload).

## Zaroori safety (Google account bachane ke liye)
1. Cloud console → Billing → **budget alert $1/month** + limit set karein. Free quota
   automatically safe nahi hota.
2. API keys ko **restrict** karein: only Places API, no referrer/IP restriction chhod dein
   (server-side hai) — ya IP whitelist kar dein.
3. `X-Goog-FieldMask` hamesha chhota: field = paisa. `places:*` mat maarein.
4. **Scraping na karein** (SerpAPI-jaisi cheezein bhi ToS-gray hain). Google Maps ko `puppeteer`
   se scrape karna aapka Google account + IP dono uda sakta hai. Maine isliye Overpass chuna.

## Ek aur "free" source jo try karne layak hai
- **Foursquare Places (free tier):** 250 calls/day? tier badalta rehta hai — `api.location.com`
  par signup, Places API se `website` milta hai. OSM ke baad doosra number.
- **OpenStreetMap Overpass + Wikidata + Wikipedia** — already free; `website` tag hi sabse acha signal.
- **Shehr/ward ki municipal business lists** (Karachi: SMC/DTIs) — PDF scraping = $0,
  magar 2 din ka kaam. Jab aap 3 mahine me 500 leads chahen, tab iski qeemat dikhti hai.
- **Swarm/Facebook "checkins"** — no API. Skip.

## Mera recommendation (senior engineer wali, marketing wali nahi)
3 mahine OSM par chalayein. `make report` me `hotness` vs actual reply-rate ka correlation dekhein.
Agar reply rate 12% se upar hai → system theek hai, **volume barhayein (zyada areas), data nahi kharidein.**
Agar reply rate <6% hai aur areas bhar poore hain → tab $5 ka Places credit lena sochein,
magar woh bhi wording fix karne ke baad.
