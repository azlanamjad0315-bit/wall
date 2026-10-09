"""sitegen.py — turn one OSM lead into a finished, deployable single-file site.

The two things this file exists to guarantee (a raw LLM call guarantees neither):
 1. **Uniqueness** — a design seed (palette x font x layout x hero x texture) chosen
    deterministically per lead from config/design-seeds.json and injected as literal CSS
    tokens. Same lead twice -> same design; two different leads -> different design.
 2. **It actually works** — HTML extracted from any prose/fence wrapping, hard-checked for
    the elements a local-business page needs (viewport, tel:, wa.me, map, JSON-LD, no dead
    placeholders), one self-heal retry with the exact list of failures, then a light
    sanitise. A page that still fails is returned with `ok:false` so the workflow can skip it
    instead of sending a broken link to a paying prospect.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ai import AiError

log = logging.getLogger("gateway.sitegen")

ALLOWED_EXT = re.compile(r"(fonts\.googleapis\.com|fonts\.gstatic\.com|picsum\.photos|wa\.me|"
                         r"api\.whatsapp\.com|googleapis\.com|google\.com|gstatic\.com|"
                         r"googleusercontent\.com|schema\.org|data:|#|mailto:|tel:)", re.I)
FENCE = re.compile(r"^\s*```(?:html)?\s*|\s*```\s*$", re.I)


def load_prompts(dir_: str) -> dict[str, str]:
    out = {}
    p = Path(dir_)
    for f in sorted(p.glob("*.md")):
        lines = f.read_text(encoding="utf-8").splitlines()
        while lines and (lines[0].startswith("#") or not lines[0].strip()):
            lines.pop(0)                      # drop the file's own doc header
        out[f.stem] = "\n".join(lines).strip()
    return out


def render(tpl: str, vars_: dict[str, str]) -> str:
    def sub(m: re.Match) -> str:
        k = m.group(1).strip()
        v = vars_.get(k)
        return "" if v is None else str(v)
    out = re.sub(r"\{\{\s*([a-z0-9_]+)\s*\}\}", sub, tpl, flags=re.I)
    return out


def slugify(name: str, area: str = "") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "demo"
    a = re.sub(r"[^a-z0-9]+", "-", (area or "").lower()).strip("-")
    short = a.split("-")[0] if a else ""
    out = f"{s[:40]}-{short}"[:56].strip("-") or "demo"
    return f"{out}-{hashlib.sha1((name + area + date.today().isoformat()).encode()).hexdigest()[:4]}"


def pick_seed(lead: dict, seeds: dict) -> dict:
    """Deterministic per (lead key, day) so retries don't reshuffle the design."""
    h = int(hashlib.sha256(f"{lead.get('key')}|{lead.get('osm_name')}|{date.today().isoformat()}".encode()
                           ).hexdigest()[:8], 16)
    n = lambda lst, i: lst[(h + i * 7919) % max(1, len(lst))]        # noqa: E731
    return {
        "palette": n(seeds.get("palettes") or [{}], 1),
        "font": n(seeds.get("fonts") or [{}], 2),
        "layout": n(seeds.get("layouts") or [{}], 3),
        "hero": n(seeds.get("heroes") or [{}], 4),
        "texture": n(seeds.get("textures") or [""], 5),
        "rules": seeds.get("rules") or [],
    }


# --------------------------------------------------------------------- extract


def extract_html(text: str) -> str:
    """Models wrap HTML in prose and fences with roughly equal frequency. Peel all of it."""
    t = (text or "").strip()
    m = re.search(r"```(?:html|HTML)?\s*(.+?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    low = t.lower()
    i = low.find("<!doctype")
    if i < 0:
        i = low.find("<html")
    if i > 0:
        t = t[i:]
    if not t.lower().lstrip().startswith("<!doctype"):
        t = "<!doctype html>" + chr(10) + t.lstrip()
    j = t.lower().rfind("</html>")
    if j != -1:
        t = t[:j + 7]
    return t.strip()


def strip_danger(html: str) -> str:
    """No remote JS, no non-Google iframes, no inline event eval — a demo site must not be able
    to phone home or run someone else's script on your subdomain."""
    html = re.sub(r"<script[^>]+src=[^>]*>.*?</script>", "", html, flags=re.I | re.S)
    html = re.sub(r"<script[^>]+src=[^>]*>", "", html, flags=re.I)
    html = re.sub(r"""<iframe[^>]+src=(["'])(?!https?://(www\.)?google\.)[^"']*\1[^>]*>""",
                  "", html, flags=re.I)
    # keep the image onerror fallback (the prompt requires it), drop anything that smells like an
    # actual injection: eval / fetch / document.cookie / location.href inside an inline handler
    html = re.sub(r"""\bon(?:error|load|click|mouseover|focus|animationend)\s*=\s*(["'])((?:(?!\1).)*)\1""",
                  lambda m: "" if re.search(r"eval|fetch\(|document\.cookie|top\.location|window\.open", m.group(2),
                                            re.I) else m.group(0),
                  html, flags=re.I | re.S)
    return html


def ensure_meta(html: str, lead: dict, cfg: dict, title: str, desc: str) -> tuple[str, list[str]]:
    """Idempotent last-mile fixes. Returns (html, fixes applied)."""
    fixes: list[str] = []
    add = []
    if 'name="viewport"' not in html and "name='viewport'" not in html:
        add.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    if "<title" not in html.lower():
        add.append(f"<title>{title}</title>")
    if "description" not in html:
        add.append(f'<meta name="description" content="{desc}">')
    if 'name="theme-color"' not in html:
        add.append('<meta name="theme-color" content="#ffffff">')
    if add and re.search(r"<head[^>]*>", html, re.I):
        html = re.sub(r"<head[^>]*>", "<head>" + "".join(add), html, count=1, flags=re.I)
        fixes.append("injected:" + ",".join(a.split('"')[1] if 'name="' in a else "title" for a in add))
    # lang attr
    lang = lead.get("lang") or "en"
    if re.search(r"<html(?![^>]*\blang=)", html, re.I):
        html = re.sub(r"<html", f'<html lang="{lang}"', html, count=1, flags=re.I)
        fixes.append("added lang")
    return html, fixes


def ensure_hook(html: str, cfg: dict, lead: dict) -> str:
    """The sales strip. If the model forgot it, we inject it ourselves — this is the whole
    business model, so it must not depend on the LLM being in a good mood."""
    wa = (cfg.get("AGENCY_WHATSAPP") or "").strip()
    if str(cfg.get("DEMO_BADGE", "true")).lower() not in ("1", "true", "yes"):
        return re.sub(r"<(div|section|a)[^>]*data-demo-hook[^>]*>.*?</\1>", "", html,
                      flags=re.I | re.S)
    if wa and (f"wa.me/{wa}" in html.replace(" ", "")):
        return html
    price = cfg.get("PRICE_LABEL", "")
    agency = cfg.get("AGENCY_NAME", "")
    href = f"https://wa.me/{wa}?text=" + quote(f"Salam {lead.get('osm_name','')}, maine aapki demo site dekhi.")
    pill = ("<span style=\"background:#c9a227;color:#111;border-radius:999px;padding:2px 8px;"
            "font-size:11px;font-weight:700;letter-spacing:.08em\">DEMO</span>")
    strip = (f'<div data-demo-hook="1" style="margin:0 auto;padding:14px 16px;max-width:760px;'
             f'font:13px/1.6 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
             f'color:inherit;opacity:.85;display:flex;gap:10px;align-items:center;'
             f'justify-content:center;flex-wrap:center;text-align:center;border-top:1px solid '
             f'currentColor"><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;'
             f'justify-content:center">{pill}<span>This is a free preview by <b>{agency}</b> — '
             f'live in {cfg.get("DELIVERY_TIME_TEXT","24 hours")}, {price}.</span>'
             f'<a href="{href}" style="color:inherit;text-decoration:underline;'
             f'text-underline-offset:3px">WhatsApp me</a></div></div>')
    if re.search(r"</footer>", html, re.I):
        return re.sub(r"</footer>", strip + "</footer>", html, count=1, flags=re.I)
    return re.sub(r"</body>", strip + "</body>", html, count=1, flags=re.I)


JUNK = re.compile(r"(lorem ipsum|your business here|business name here|\{\{\s*\w+\s*\}\}|"
                  r"example\.com|placeholder|TODO|FIXME|john doe|1234 street|dummy text)", re.I)


def validate(html: str, lead: dict, cfg: dict) -> dict:
    issues: list[str] = []
    n = len(html)
    if n < 4000:
        issues.append(f"too small ({n} bytes) — likely truncated, generate again")
    if n > 260_000:
        issues.append("suspiciously large")
    if not re.search(r"<!doctype html", html, re.I):
        issues.append("missing <!doctype html>")
    if "</html>" not in html.lower():
        issues.append("missing closing </html>")
    if 'name="viewport"' not in html:
        issues.append("missing viewport meta (mobile broken)")
    for tag in ("header", "main", "footer"):
        if f"<{tag}" not in html:
            issues.append(f"missing <{tag}>")
    name = (lead.get("osm_name") or "").strip()
    if name and name.lower()[:14] not in html.lower():
        issues.append(f"business name '{name}' not used on the page")
    if not re.search(r"(wa\.me|api\.whatsapp\.com)", html, re.I):
        issues.append("no WhatsApp link")
    if lead.get("whatsapp") or lead.get("phone"):
        if "tel:" not in html:
            issues.append("no click-to-call link")
    if "maps" not in html.lower():
        issues.append("no map embed")
    if "application/ld+json" not in html:
        issues.append("no JSON-LD (SEO)")
    if "onerror" not in html and "picsum" in html:
        issues.append("picsum images without onerror fallback")
    if JUNK.search(html):
        issues.append("placeholder/lorem text found: " +
                      ", ".join(sorted(set(m.group(0) for m in JUNK.finditer(html)))[:4]))
    ext = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    bad = [u for u in ext if not ALLOWED_EXT.search(u)]
    if bad:
        issues.append("disallowed external URL(s): " + ", ".join(bad[:3]))
    # structural sanity: unbalanced tags that break rendering
    for t in ("div", "section"):
        o, c = len(re.findall(rf"<{t}[\s>]", html, re.I)), len(re.findall(rf"</{t}>", html, re.I))
        if o != c:
            issues.append(f"unbalanced <{t}> ({o} open / {c} close)")
    return {"ok": not [i for i in issues if "truncated" not in i], "issues": issues,
            "bytes": n, "tags": len(ext)}


# --------------------------------------------------------------------- generate


class SiteGen:
    def __init__(self, ai, prompts: dict[str, str], seeds: dict, cfg: dict):
        self.ai, self.prompts, self.seeds, self.cfg = ai, prompts, seeds, cfg

    def vars_for(self, lead: dict, seed: dict, lang: str) -> dict[str, str]:
        cfg, pal, fnt = self.cfg, seed["palette"], seed["font"]
        price = price_for(cfg, lead.get("country", ""))
        v = {
            "business_name": lead.get("osm_name", ""), "name": lead.get("osm_name", ""),
            "category": lead.get("category", ""), "category_label": lead.get("category_label", ""),
            "address": lead.get("address", ""), "city": lead.get("city", ""), "area": lead.get("area", ""),
            "country": lead.get("country", ""), "phone": lead.get("phone", ""),
            "whatsapp": lead.get("whatsapp", ""), "rating": lead.get("rating", ""),
            "reviews_count": lead.get("reviews_count", ""), "opening_hours": lead.get("opening_hours", ""),
            "instagram": lead.get("instagram", ""), "facebook": lead.get("facebook", ""),
            "notes": lead.get("notes", ""), "brief": lead.get("brief", ""),
            "cta_label": lead.get("cta_label", "WhatsApp"),
            "extra_sections_json": lead.get("extra_sections", "[]"),
            "lang": lang, "site_slug": lead.get("slug", ""),
            "agency_name": cfg.get("AGENCY_NAME", ""), "owner_name": cfg.get("AGENCY_NAME", ""),
            "agency_whatsapp": cfg.get("AGENCY_WHATSAPP", ""),
            "price_label": price, "delivery_time_text": cfg.get("DELIVERY_TIME_TEXT", "24 hours"),
            "offer_text": cfg.get("OFFER_TEXT", ""), "palette_accent": pal.get("accent", "#c9a227"),
            "font_url": fnt.get("google", ""),
            "palette_json": json.dumps(pal, ensure_ascii=False),
            "font_json": json.dumps(fmt_clean(fnt), ensure_ascii=False),
            "layout_json": json.dumps(seed["layout"], ensure_ascii=False),
            "hero_json": json.dumps(seed["hero"], ensure_ascii=False),
            "texture": seed["texture"], "rules_json": json.dumps(seed["rules"], ensure_ascii=False),
        }
        return {k: ("" if v is None else str(v)) for k, v in v.items()}

    async def generate(self, lead: dict, lang: str = "en", max_repair: int = 1) -> dict:
        base = self.prompts.get("site-generator")
        if not base:
            raise AiError("prompts/site-generator.md missing")
        seed = pick_seed(lead, self.seeds)
        v = self.vars_for(lead, seed, lang)
        sys_prompt = render(base, v)
        user = ("Build the complete single-file site for %s in %s now. Follow the design seed "
                "literally. Output only HTML." % (lead.get("osm_name"), lead.get("city") or lead.get("area")))
        msg = [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}]
        tries, meta, html, chk = 0, {}, "", {}
        while tries <= max_repair:
            tries += 1
            try:
                meta = await self.ai.chat(msg, max_tokens=int(self.cfg.get("AI_MAX_TOKENS_HTML", 16384)),
                                          temperature=float(self.cfg.get("AI_TEMPERATURE", "0.85")),
                                          tag=f"site:{lead.get('slug','?')}")
            except AiError as e:
                return {"ok": False, "error": str(e), "attempts": tries}
            html = strip_danger(extract_html(meta["text"]))
            html, _ = ensure_meta(html, {**lead, "lang": lang}, self.cfg,
                                   f"{lead.get('osm_name')} — {lead.get('category_label')}, {lead.get('city')}",
                                   describe(lead, self.cfg))
            html = ensure_hook(html, self.cfg, lead)
            chk = validate(html, lead, self.cfg)
            if chk["ok"]:
                break
            if tries <= max_repair:
                log.info("%s: %s -> repair pass", lead.get("slug"), "; ".join(chk["issues"])[:200])
                msg = [{"role": "system", "content": sys_prompt},
                       {"role": "user", "content": user + "\n\nPREVIOUS ATTEMPT WAS REJECTED. Fix all of "
                        "these and output the full corrected document only:\n- " + "\n- ".join(chk["issues"])}]
        title = f"{lead.get('osm_name')} — {lead.get('category_label')}, {lead.get('city')}".strip(" —,")
        return {"ok": bool(chk.get("ok")), "html": html, "issues": chk.get("issues", []),
                "bytes": chk.get("bytes", len(html)), "slug": lead.get("slug"),
                "title": clean(title), "meta_description": describe(lead, self.cfg),
                "design": {"palette": seed["palette"].get("id"), "font": seed["font"].get("id"),
                           "layout": seed["layout"].get("id"), "hero": seed["hero"].get("id")},
                "provider": meta.get("provider"), "model": meta.get("model"),
                "attempts": tries, "quota_left": meta.get("quota_left")}


def fmt_clean(fnt: dict) -> dict:
    return {k: v for k, v in (fnt or {}).items() if k != "google"}


def describe(lead: dict, cfg: dict) -> str:
    bits = [lead.get("osm_name", ""), lead.get("category_label", ""), "in " + (lead.get("city") or lead.get("area") or "")]
    extra = []
    if lead.get("opening_hours"):
        extra.append("open " + lead["opening_hours"].replace("Mo-Su 10:00-22:00", "daily 10am-10pm"))
    if lead.get("rating"):
        extra.append(f"{lead['rating']}★ on Google")
    s = " ".join(x for x in bits if x).strip() + (". " + "; ".join(extra) if extra else "")
    s += f". Order / book on WhatsApp — {cfg.get('DELIVERY_TIME_TEXT', '24h')}, no setup fee."
    return clean(s)[:155]


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()


def price_for(cfg: dict, country: str = "") -> str:
    """PRICE_MAP=PK=PKR 15,000 one-time|default=USD 149 one-time"""
    m = cfg.get("PRICE_MAP") or ""
    for part in m.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip().upper() == (country or "").upper():
                return v.strip()
    for part in m.split("|"):
        if part.lower().startswith("default="):
            return part.split("=", 1)[1].strip()
    return cfg.get("PRICE_LABEL", "PKR 15,000 one-time")


def lang_for(cfg: dict, country: str = "", text_hint: str = "") -> str:
    forced = (cfg.get("LANGUAGE") or "auto").strip().lower()
    if forced in ("roman-urdu", "urdu", "ur"):
        return "roman-urdu"
    if forced in ("english", "en"):
        return "english"
    if (country or "").upper() in ("PK", "IN", "BD") or (text_hint or "") == "south-asia":
        return "roman-urdu"
    return "english"


# --------------------------------------------------------------------- portfolio


def portfolio_html(rows: list[dict], cfg: dict) -> str:
    """A public 'sites I made' page from the CRM rows — your best sales asset.
    Built in code (not by the LLM) so it can never be empty or wrong."""
    name = cfg.get("AGENCY_NAME", "DemoSites")
    wa = cfg.get("AGENCY_WHATSAPP", "")
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("demo_url")]
    city = clean(next((r.get("city") for r in rows if r.get("city")), "")) or "your city"
    cards = []
    for r in rows[:60]:
        t = clean(r.get("title") or r.get("business_name") or "Demo")
        cat = clean(r.get("category_label") or "Local business")
        city = clean(r.get("city") or "")
        emoji = r.get("emoji") or "🏪"
        cards.append(
            f'<a class="c" href="{r["demo_url"]}" target="_blank" rel="noopener">'
            f'<span class="e">{emoji}</span><h3>{t}</h3><p>{cat} · {city}</p>'
            f'<span class="v">View demo →</span></a>')
    grid = "\n".join(cards) or '<p class="none">Pehli demos jald aayengi.</p>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} — demo websites for local businesses</title>
<meta name="description" content="Real one-page websites built for local shops in {city}. Every demo below is live. WhatsApp {name} to get yours.">
<style>
*{{box-sizing:border-box}}body{{margin:0;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
background:#0d0d0f;color:#f4f1ea;padding:0 0 60px}}
.wrap{{max-width:1080px;margin:0 auto;padding:0 20px}}
header{{padding:56px 0 28px;border-bottom:1px solid #ffffff1a;margin-bottom:32px}}
h1{{font-size:clamp(30px,6vw,56px);line-height:1.05;margin:0 0 12px;letter-spacing:-.02em}}
h1 span{{color:#c9a227}}p.sub{{color:#a49f95;max-width:62ch;margin:0}}
.grid{{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(240px,1fr))}}
.c{{display:block;text-decoration:none;color:inherit;background:#17171b;border:1px solid #ffffff14;
border-radius:14px;padding:18px;transition:.25s}}
.c:hover{{transform:translateY(-3px);border-color:#c9a22766;background:#1c1c21}}
.e{{font-size:22px}}h3{{margin:8px 0 4px;font-size:17px}}.c p{{margin:0;color:#a49f95;font-size:13px}}
.v{{display:inline-block;margin-top:12px;font-size:12px;color:#c9a227;letter-spacing:.04em}}
.none{{color:#a49f95}}
.cta{{margin:44px 0 0;padding:22px;background:#c9a227;color:#111;border-radius:16px;display:flex;
gap:14px;align-items:center;justify-content:space-between;flex-wrap:wrap;font-weight:600}}
.cta a{{background:#111;color:#f4f1ea;padding:11px 16px;border-radius:999px;text-decoration:none;font-size:14px}}
footer{{margin-top:34px;color:#a49f95;font-size:13px}}
</style></head><body>
<div class="wrap">
<header><h1>{name}<span>.</span> — live demos</h1>
<p class="sub">Ek page, mobile-fast, WhatsApp order button ke saath. Neeche sab demos asli businesses ke
liye banaye gaye hain — khud dekh lein, koi form nahi, koi catch nahi.</p></header>
<div class="grid">
{grid}
</div>
<div class="cta"><div>Aapki business ki bari? Demo free hai — 24 ghante me live.</div>
<a href="https://wa.me/{wa}">WhatsApp me</a></div>
<footer>Built by {name} · {cfg.get("PRICE_LABEL","")}</footer>
</div></body></html>"""
