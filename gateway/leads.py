"""leads.py — lead discovery with zero paid APIs.

Pipeline: Nominatim geocode → Overpass bbox query (per niche) → normalise OSM element
→ "no real website" filter → score → dedupe against keys n8n passed from the CRM.

Why OpenStreetMap instead of Google Places: Google Maps Platform now needs a card and
caps the free tier (and charges silently after 10k calls). OSM/Overpass is free with no
key at all. Trade-off we accept and document: OSM coverage in Pakistan is ~85-90% for
restaurants/salons but weaker for brand-new shops; Google rating/review data is NOT in OSM
for most POIs, so rating is usually empty and the AI is instructed not to invent it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx

log = logging.getLogger("gateway.leads")

_UA = "DemoSitesAI/1.0 (self-hosted lead radar; contact: %s)"
_BLOCK_TAGS = ("shop", "amenity", "leisure", "office", "tourism", "craft", "healthcare", "sport")
_SOCIAL = re.compile(r"(instagram\.com|facebook\.com|fb\.me|wa\.me|whatsapp\.com|tiktok\.com|"
                     r"youtube\.com|snapchat\.com|twitter\.com|x\.com|linkedin\.com)", re.I)
_JUNK_SITE = re.compile(r"(wixsite\.com/weebly|blogspot\.|wordpress\.com/|wix\.com/|sites\.google\.com|"
                        r"business\.site|godaddysites|\.elementor\.com|dpd0|weebly\.com)", re.I)
_NOISE_NAME = re.compile(r"^(open ?24|new |khan |.* nahi )", re.I)



# ------------------------------------------------------------------ Nominatim


class Geo:
    """Nominatim usage policy: identify yourself, <=1 req/s, cache aggressively."""

    def __init__(self, cache_dir: str, url: str, email: str, ttl_hours: int = 24 * 90):
        self.base = url.rstrip("/")
        self.cache = Path(cache_dir) / "geo.json"
        self.ttl = ttl_hours * 3600
        self.email = email
        self._lock = asyncio.Lock()
        self._last = 0.0
        try:
            self._mem: dict[str, Any] = json.loads(self.cache.read_text())
        except Exception:
            self._mem = {}

    def _save(self) -> None:
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps(self._mem))
        except Exception as e:
            log.debug("geo cache save failed: %s", e)

    async def geocode(self, query: str, country: str = "") -> dict | None:
        key = hashlib.sha1(f"{query}|{country}".encode()).hexdigest()[:16]
        hit = self._mem.get(key)
        if hit and time.time() - hit["t"] < self.ttl:
            return hit["v"]
        async with self._lock:                       # global 1 req/s
            gap = 1.15 - (time.monotonic() - self._last)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last = time.monotonic()
            q = query if not country else f"{query}, {country}"
            url = (f"{self.base}/search?format=jsonv2&limit=1&q={quote_plus(q)}"
                   "&addressdetails=1")
            try:
                async with httpx.AsyncClient(timeout=25) as cx:
                    r = await cx.get(url, headers={"User-Agent": _UA % (self.email or "unknown")})
                    r.raise_for_status()
                    rows = r.json()
            except Exception as e:
                log.warning("geocode %r failed: %s", query, e)
                return hit["v"] if hit else None
        if not rows:
            return None
        g = rows[0]
        bb = g.get("boundingbox") or []
        val = {
            "display_name": g.get("display_name", ""),
            "lat": float(g.get("lat", 0)), "lon": float(g.get("lon", 0)),
            "bbox": None,
            "type": g.get("type", ""),
            "addr": {k: v for k, v in (g.get("address") or {}).items()
                     if k in ("city", "town", "village", "suburb", "district", "county",
                              "state", "postcode", "country", "country_code")},
        }
        if len(bb) == 4:
            # API order: south,west,north,east
            val["bbox"] = [float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])]
        self._mem[key] = {"t": time.time(), "v": val}
        self._save()
        return val


# ------------------------------------------------------------------ Overpass


class Radar:
    def __init__(self, cfg: dict, geo: Geo, cache_dir: str):
        self.cfg = cfg
        self.geo = geo
        self.radius = int(cfg.get("LEADS_RADIUS_METERS", 6000))
        self.mirrors = [m.strip() for m in cfg.get("OVERPASS_MIRRORS", "").split(",") if m.strip()] \
            or [cfg.get("OVERPASS_URL", "https://overpass-api.de/api/interpreter")]
        self.cache_hours = float(cfg.get("LEADS_CACHE_HOURS", 24))
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.verify_site = str(cfg.get("VERIFY_NO_WEBSITE", "false")).lower() in ("1", "true", "yes")
        self.cooldown_days = int(cfg.get("LEADS_COOLDOWN_DAYS", 30))
        self.niches = json.loads(Path(cfg["NICHES_FILE"]).read_text())["niches"]

    # -- query -----------------------------------------------------------
    def build_query(self, bbox: list[float], niche_keys: list[str], limit: int) -> str:
        sw_lat, sw_lon, ne_lat, ne_lon = bbox
        stmts: list[str] = []
        for k in niche_keys:
            n = self.niches.get(k)
            if not n:
                continue
            for sel in n["selectors"]:
                s = sel.strip().rstrip(";")
                if "bbox" not in s:
                    continue
                if not self.verify_site:                    # cheaper payload: ask OSM for
                    s = re.sub(r"\[\"name\"\]",
                               '[\"name\"][\"website\"!~\".\"][\"contact:website\"!~\".\"]'
                               '[\"url\"!~\".\"][\"official_url\"!~\".\"]', s, count=1)
                stmts.append(s + ";")
        union = "\n  ".join(dict.fromkeys(stmts))
        return (f'[out:json][timeout:90][bbox:{sw_lat},{sw_lon},{ne_lat},{ne_lon}];\n(\n'
                f'  {union}\n);\nout tags center {limit};')

    async def _fetch(self, ql: str) -> dict | None:
        async with httpx.AsyncClient(timeout=150) as cx:
            for url in self.mirrors:
                for attempt in range(2):
                    try:
                        r = await cx.post(url, data={"data": ql},
                                          headers={"Content-Type": "application/x-www-form-urlencoded",
                                                   "User-Agent": _UA % self.cfg.get("CONTACT_EMAIL", "")})
                    except Exception as e:
                        log.warning("overpass %s net err %s", url, e)
                        await asyncio.sleep(3 + 7 * attempt)
                        continue
                    if r.status_code == 200:
                        try:
                            return r.json()
                        except Exception:
                            log.warning("overpass %s returned non-json", url)
                            break
                    if r.status_code in (429, 504, 500, 503):
                        log.info("overpass %s -> %s, retrying mirror", url, r.status_code)
                        await asyncio.sleep(6 * (attempt + 1))
                        continue
                    break
        return None

    # -- bbox ------------------------------------------------------------
    async def bbox_for(self, area: str, country: str) -> list[float] | None:
        g = await self.geo.geocode(area, country)
        if not g:
            return None
        if g.get("bbox"):
            # A whole-city bbox returns 50k POIs; clamp to a walkable radius around the centroid.
            big = g["bbox"]
            span = max(big[2] - big[0], big[3] - big[1])
            if span * 111_000 <= self.radius * 2.4:
                return big
            lat, lon = g["lat"], g["lon"]
            d_lat = self.radius / 111_320.0
            d_lon = self.radius / (111_320.0 * max(0.2, math.cos(math.radians(lat))))
            return [lat - d_lat, lon - d_lon, lat + d_lat, lon + d_lon]
        lat, lon = g["lat"], g["lon"]
        d = self.radius / 111_320.0
        return [lat - d, lon - d, lat + d, lon + d]

    # -- normalise -------------------------------------------------------
    @staticmethod
    def _clean_url(u: str) -> str:
        u = (u or "").strip().rstrip(",;")
        if u and not u.startswith(("http://", "https://")):
            u = "https://" + u
        return u

    @staticmethod
    def phone_of(t: dict) -> str:
        for k in ("phone", "contact:phone", "contact:mobile", "mobile", "call"):
            v = (t.get(k) or "").split(";")[0].split(",")[0].strip()
            if v:
                return v
        return ""

    @staticmethod
    def dial_code(country: str) -> str:
        return {"PK": "92", "IN": "91", "BD": "880", "AE": "971", "SA": "966", "GB": "44",
                "US": "1", "CA": "1", "AU": "61", "MY": "60", "ID": "62", "TR": "90",
                "ZA": "27", "NG": "234", "KE": "254", "EG": "20", "QA": "974", "OM": "968",
                "BH": "973", "KW": "965", "DE": "49", "FR": "33", "IT": "39", "ES": "34",
                "NL": "31", "SE": "46", "SG": "65"}.get((country or "").upper(), "")

    @staticmethod
    def whatsapp_of(t: dict, phone: str, country: str = "") -> str:
        raw = (t.get("contact:whatsapp") or t.get("whatsapp") or phone or "").strip()
        d = re.sub(r"\D", "", raw)
        if not d:
            return ""
        if d.startswith("00"):
            d = d[2:]
        if d.startswith("0") and len(d) <= 11:          # national format -> needs country code
            d = Radar.dial_code(country) + d.lstrip("0")
        elif len(d) in (9, 10) and country:              # bare local number
            d = Radar.dial_code(country) + d
        if not (8 <= len(d) <= 15):
            return ""
        return d

    @staticmethod
    def addr_of(t: dict) -> tuple[str, str]:
        parts = [t.get(f) for f in ("addr:street", "addr:housenumber", "addr:neighbourhood",
                                    "addr:suburb", "addr:district", "addr:city", "addr:town",
                                    "addr:village", "addr:postcode")]
        line = ", ".join(x for x in [
            (f"{t.get('addr:housenumber','')} {t.get('addr:street','')}").strip(),
            t.get("addr:neighbourhood") or t.get("addr:suburb") or "", t.get("addr:block") or "",
        ] if x)
        city = t.get("addr:city") or t.get("addr:town") or t.get("addr:village") or ""
        if not line:
            line = ", ".join(x for x in parts[2:] if x)
        return line.strip(", "), (city or "").strip()

    def tagmap(self) -> dict[str, dict[str, str]]:
        """Compile {osm_tag: {tag_value: niche_key}} straight from config/niches.json so the
        query file and the classifier can never drift apart."""
        if getattr(self, "_tagmap_cache", None):
            return self._tagmap_cache
        m: dict[str, dict[str, str]] = {}
        for key, n in self.niches.items():
            blob = " ".join(n.get("selectors") or [])
            for tag, alt in re.findall(r'\["([a-z:_]+)"~"\^\(([^)]+)\)\$"\]', blob):
                for v in alt.split("|"):
                    m.setdefault(tag, {})[v.strip()] = key
            for tag, pat in re.findall(r'\["([a-z:_]+)"~"([^"()|]*[^"]*)"\]', blob):
                for v in re.findall(r"[a-z_]+", pat):
                    m.setdefault(tag, {}).setdefault(v, key)
            for tag, val in re.findall(r'\["([a-z:_]+)"="([^"]+)"\]', blob):
                m.setdefault(tag, {})[val] = key
        self._tagmap_cache = m
        return m

    def categorise(self, t: dict) -> str:
        m = self.tagmap()
        for tag in _BLOCK_TAGS:
            v = t.get(tag)
            if v and v in m.get(tag, {}):
                return m[tag][v]
        for tag in _BLOCK_TAGS:
            v = t.get(tag)
            if not v:
                continue
            rx = m.get(tag, {})
            for val, key in rx.items():
                if val and val in v:
                    return key
        return "services"

    def normalise(self, el: dict, area: str, campaign: dict) -> dict | None:
        t = el.get("tags") or {}
        name = (t.get("name") or "").strip()
        if len(name) < 2 or _NOISE_NAME.match(name) and " " not in name:
            return None
        website = self._clean_url(t.get("website") or t.get("contact:website") or t.get("url")
                                  or t.get("official_url") or "")
        phone = self.phone_of(t)
        social = ""
        for k in ("contact:instagram", "instagram", "contact:facebook", "facebook", "contact:phone:mobile"):
            if t.get(k):
                social = self._clean_url(t[k])
                break
        has_social = bool(_SOCIAL.search(website or "")) or bool(social)
        # THE core filter: no usable website of their own
        real_site = bool(website) and not has_social and not _JUNK_SITE.search(website)
        cat = self.categorise(t)
        if real_site and not self.verify_site:
            return None
        line, city = self.addr_of(t)
        lat, lon = el.get("lat") or (el.get("center") or {}).get("lat"), \
                   el.get("lon") or (el.get("center") or {}).get("lon")
        rating, nrev = "", ""
        try:
            rating = str(float(t.get("rating") or t.get("review:rating") or "")) if (t.get("rating") or t.get("review:rating")) else ""
        except Exception:
            rating = ""
        nrev = str(t.get("reviews") or t.get("review:count") or "")
        note_bits = [t.get(k) for k in ("cuisine", "outdoor_seating", "delivery", "takeaway",
                                        "opening_hours", "description", "brand", "operator")]
        notes = "; ".join(f"{k}={v}" for k, v in
                          zip(("cuisine", "outdoor_seating", "delivery", "takeaway", "hours",
                               "description", "brand", "operator"),
                              [x for x in note_bits if x]))
        lead_country = (t.get("addr:country") or campaign.get("country") or "").upper()
        key = f"{el.get('type','n')}/{el.get('id')}"
        lead = {
            "key": key,
            "osm_name": name,
            "category": cat,
            "category_label": self.niches.get(cat, {}).get("label", "Local business"),
            "cta_label": self.niches.get(cat, {}).get("cta_label", "WhatsApp"),
            "brief": self.niches.get(cat, {}).get("brief", ""),
            "extra_sections": json.dumps(self.niches.get(cat, {}).get("extra_sections", [])),
            "emoji": self.niches.get(cat, {}).get("emoji", "🏪"),
            "address": line or area,
            "city": city or campaign.get("city_hint", "") or area.split(",")[-1].strip(),
            "area": area,
            "country": (t.get("addr:country") or campaign.get("country") or "").upper(),
            "phone": phone,
            "whatsapp": self.whatsapp_of(t, phone, lead_country),
            "website": website,
            "social": social or (website if has_social else ""),
            "rating": rating, "reviews_count": nrev,
            "opening_hours": t.get("opening_hours", "") or "",
            "instagram": social if "insta" in (social or "") else "",
            "facebook": social if "facebook" in (social or "") or "fb.me" in (social or "") else "",
            "lat": lat or "", "lon": lon or "",
            "notes": (notes + ("; " if notes and t.get("addr:full") else "")).strip(),
            "contactable": bool(phone or website or social),
        }
        lead["hotness"] = self.score(lead)
        lead["site_ok_to_send"] = True
        return lead

    @staticmethod
    def score(l: dict) -> int:
        """0-100. Prioritise 'has money + reachable + actually needs a site'."""
        s = 30
        if l.get("whatsapp") or l.get("phone"):
            s += 22
        try:
            r = float(l.get("rating") or 0)
            if r >= 4.3:
                s += 18
            elif r >= 4.0:
                s += 10
        except Exception:
            pass
        try:
            n = int(float(l.get("reviews_count") or 0))
            s += min(20, n // 5)
        except Exception:
            pass
        if l.get("website"):
            s -= 12          # had a junk/social-only site → still worth pitching, less
        if l.get("opening_hours"):
            s += 5
        if l.get("address") and len(l["address"]) > 6:
            s += 5
        if l.get("social"):
            s += 6           # they already invest in marketing
        return max(0, min(100, s))

    async def dead_site(self, url: str) -> bool:
        """True if a listed website is parked/dead/JS-less junk."""
        if not url:
            return False
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as cx:
                r = await cx.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; DemoSitesAI)"})
                if r.status_code >= 400:
                    return True
                body = r.text[:4000].lower()
                return any(x in body for x in ("domain is for sale", "under construction",
                                               "coming soon", "buy this domain", "parkingcrew",
                                               "this page could not be found"))
        except Exception:
            return True      # unreachable == no working website

    # -- orchestration ---------------------------------------------------
    async def scan(self, campaign: dict, areas: list[str] | None = None,
                   niches: list[str] | None = None, limit_per_area: int = 12,
                   exclude_keys: list[str] | None = None, min_hotness: int = 0,
                   require_contact: bool = True) -> dict:
        exclude = set(exclude_keys or [])
        niches = niches or list(self.niches.keys())
        areas = areas or campaign.get("areas") or []
        country = campaign.get("country", "")
        out: list[dict] = []
        seen: set[str] = set()
        stats = {"areas": 0, "osm_raw": 0, "no_website": 0, "deduped": 0, "errors": []}

        for area in areas:
            bbox = await self.bbox_for(area, country)
            if not bbox:
                stats["errors"].append(f"geocode failed: {area}")
                continue
            stats["areas"] += 1
            ck = hashlib.sha1(json.dumps([bbox, niches, limit_per_area]).encode()).hexdigest()[:20]
            cf = self.cache_dir / f"ov-{ck}.json"
            data = None
            if cf.exists() and self.cache_hours > 0:
                age_h = (datetime.now(timezone.utc) - datetime.fromtimestamp(cf.stat().st_mtime,
                                                                            timezone.utc)).total_seconds() / 3600
                if age_h < self.cache_hours:
                    try:
                        data = json.loads(cf.read_text())
                    except Exception:
                        data = None
            if data is None:
                data = await self._fetch(self.build_query(bbox, niches, limit_per_area * 4))
                if data:
                    try:
                        cf.write_text(json.dumps(data))
                    except Exception:
                        pass
            if not data:
                stats["errors"].append(f"overpass empty/timeout: {area}")
                continue
            stats["osm_raw"] += len(data.get("elements") or [])
            kept = 0
            for el in data.get("elements") or []:
                try:
                    lead = self.normalise(el, area, campaign)
                except Exception as e:
                    log.debug("normalise failed: %s", e)
                    continue
                if not lead or lead["key"] in seen:
                    continue
                if lead["key"] in exclude:
                    stats["deduped"] += 1
                    continue
                if require_contact and not lead["contactable"]:
                    continue
                if lead["hotness"] < min_hotness:
                    continue
                if self.verify_site and lead["website"]:
                    if not await self.dead_site(lead["website"]):
                        continue
                seen.add(lead["key"])
                stats["no_website"] += 1
                out.append(lead)
                kept += 1
                if kept >= limit_per_area:
                    break
        out.sort(key=lambda x: -x["hotness"])
        return {"leads": out, "stats": stats,
                "campaign": campaign.get("name"), "generated_at": datetime.now(timezone.utc).isoformat()}
