"""app.py — the only HTTP surface n8n talks to.

Design rule for this whole project: n8n nodes stay dumb (HTTP + Code + Switch), every bit of
fragile logic (retries, fallbacks, rate budgets, HTML fixing, deploy liveness) lives here where it
can be unit-tested with `make test`.

Endpoints (POST = flat JSON body, all fields optional unless marked):
  GET  /healthz                     -> {ok, providers, quotas_left, deploy_target}
  POST /v1/leads/scan               -> {leads:[...], stats}          (campaign|areas, niches, limit)
  POST /v1/demo/generate            -> {html, slug, design, ok, ...} (lead fields required: name)
  POST /v1/site/deploy              -> {url, live}                   (slug+html required)
  POST /v1/site/portfolio           -> {url}                          (rows_json)
  POST /v1/outreach                 -> {message, language, chat_id}  (business_name, demo_url)
  POST /v1/reply/classify           -> {intent,...}                  (message_text required)
  GET  /preview/<slug>/             -> local demo site (DEPLOY_TARGET=local)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ai import AiClient, AiError, Budget, build_chain
from deploy import DeployError, Deployer
from leads import Geo, Radar
from sitegen import (SiteGen, clean, lang_for, load_prompts, portfolio_html, price_for,
                     render, slugify, validate)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("gateway")

DATA_DIR = os.getenv("DATA_DIR", "/data")
PROMPTS_DIR = os.getenv("PROMPTS_DIR", "/app/prompts")
CONFIG_DIR = os.getenv("CONFIG_DIR", "/app/config")
TOKEN = (os.getenv("GATEWAY_TOKEN") or "").strip()
Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


def env(k: str, d: str = "") -> str:
    return (os.getenv(k) or d).strip()


CFG: dict[str, Any] = {
    "LEADS_RADIUS_METERS": env("LEADS_RADIUS_METERS", "6000"),
    "LEADS_CACHE_HOURS": env("LEADS_CACHE_HOURS", "24"),
    "LEADS_COOLDOWN_DAYS": env("LEADS_COOLDOWN_DAYS", "30"),
    "OVERPASS_URL": env("OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
    "OVERPASS_MIRRORS": env("OVERPASS_MIRRORS", ""),
    "VERIFY_NO_WEBSITE": env("VERIFY_NO_WEBSITE", "false"),
    "CONTACT_EMAIL": env("CONTACT_EMAIL") or env("NOMINATIM_EMAIL"),
    "NICHES_FILE": str(Path(CONFIG_DIR) / "niches.json"),
    "AGENCY_NAME": env("AGENCY_NAME", "DemoSites"),
    "AGENCY_WHATSAPP": env("WHATSAPP_NUMBER") or re.sub(r"\D", "", env("CONTACT_PHONE", "")),
    "DELIVERY_TIME_TEXT": env("DELIVERY_TIME_TEXT", "24 hours"),
    "PRICE_LABEL": env("PRICE_LABEL", "PKR 15,000 one-time"),
    "PRICE_MAP": env("PRICE_MAP", ""),
    "OFFER_TEXT": env("OFFER_TEXT", ""),
    "DEMO_BADGE": env("DEMO_BADGE", "true"),
    "LANGUAGE": env("LANGUAGE", "auto"),
    "DEPLOY_TARGET": env("DEPLOY_TARGET", "local"),
    "GITHUB_TOKEN": env("GITHUB_TOKEN"), "GITHUB_REPO": env("GITHUB_REPO"),
    "GITHUB_BRANCH": env("GITHUB_BRANCH", "main"), "PAGES_BASE_URL": env("PAGES_BASE_URL"),
    "CF_ACCOUNT_ID": env("CF_ACCOUNT_ID"), "CF_PAGES_PROJECT": env("CF_PAGES_PROJECT"),
    "CF_API_TOKEN": env("CF_API_TOKEN"), "PUBLIC_PREVIEW_URL": env("PUBLIC_PREVIEW_URL"),
    "DATA_DIR": DATA_DIR, "AI_MAX_TOKENS": env("AI_MAX_TOKENS_HTML") or env("AI_MAX_TOKENS", "16384"),
    "AI_TEMPERATURE": env("AI_TEMPERATURE", "0.85"),
    "PAY_METHODS": env("PAY_METHODS", ""), "PAY_ACCOUNT_NAME": env("PAY_ACCOUNT_NAME", ""),
    "PAY_INSTRUCTIONS": env("PAY_INSTRUCTIONS", ""),
    "MAX_AUTO_REPLIES": env("AUTO_REPLY_MAX_PER_THREAD", "2"),
    "SEEDS_FILE": str(Path(CONFIG_DIR) / "design-seeds.json"),
    "CAMPAIGNS_FILE": str(Path(CONFIG_DIR) / "campaigns" / "default.json"),
}

CHAIN = build_chain()
AI = AiClient(CHAIN, Budget(DATA_DIR))
PROMPTS = load_prompts(PROMPTS_DIR)
SEEDS: dict = {}
try:
    SEEDS = json.loads(Path(CFG["SEEDS_FILE"]).read_text())
except Exception as e:                                                   # pragma: no cover
    log.error("design seeds: %s", e)
GEO = Geo(DATA_DIR, env("NOMINATIM_URL", "https://nominatim.openstreetmap.org"), CFG["CONTACT_EMAIL"])
RADAR = Radar(CFG, GEO, DATA_DIR)
SITEGEN = SiteGen(AI, PROMPTS, SEEDS, CFG)
DEPLOYER = Deployer(CFG)
STATE_FILE = Path(DATA_DIR) / "state.json"
_lock = asyncio.Lock()

app = FastAPI(title="demosites ai-gateway", version="1.0.0")


# ------------------------------------------------------------------- plumbing


def auth(x_gateway_token: str | None = Header(default=None), authorization: str | None = Header(default=None)) -> None:
    if not TOKEN:
        return
    given = (x_gateway_token or "").strip()
    if not given and authorization and authorization.lower().startswith("bearer "):
        given = authorization[7:].strip()
    if given != TOKEN:
        raise HTTPException(401, "bad gateway token")


def state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"deployed": {}, "hashes": [], "sent": {}}


def save_state(s: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(s))
    except Exception as e:                                                # pragma: no cover
        log.debug("state save failed: %s", e)


async def body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        b = {}
    if not isinstance(b, dict):
        raise HTTPException(400, "JSON object body expected")
    # form-encoded also works (handy for quick curl tests)
    if not b:
        try:
            form = await request.form()
            b = {k: v for k, v in form.items()}
        except Exception:
            b = {}
    for k, v in list(b.items()):
        if isinstance(v, str) and v.strip().startswith(("{", "[")):
            try:
                b[k] = json.loads(v)
            except Exception:
                pass
    return b


def s(b: dict, k: str, d: str = "") -> str:
    v = b.get(k, d)
    if v is None:
        return d
    return str(v).strip()


# --------------------------------------------------------------------- health


@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": int(time.time()), "deploy_target": CFG["DEPLOY_TARGET"],
            "prompts": sorted(PROMPTS.keys()), "niches": sorted(RADAR.niches.keys()),
            "providers": [{"name": p.name, "model": p.model, "today_left": AI.budget.left(p.name)}
                          for p in CHAIN] or "NONE — set GEMINI_API_KEY or GROQ_API_KEY",
            "warn": ("" if any(p.style != "ollama" for p in CHAIN) else
                     "local Ollama only: works, but set GEMINI_API_KEY (free) for much better sites"),
            "waha": bool(env("WAHA_URL")), "telegram": bool(env("TELEGRAM_BOT_TOKEN")),
            "crm": env("CRM_BACKEND", "baserow")}


# ---------------------------------------------------------------------- leads


def load_campaigns() -> list[dict]:
    try:
        raw = json.loads(Path(CFG["CAMPAIGNS_FILE"]).read_text())
    except Exception as e:
        log.error("campaigns file: %s", e)
        return []
    rows = [c for c in raw.get("campaigns", []) if isinstance(c, dict) and c.get("enabled", True)]
    want = {x.strip() for x in env("CAMPAIGNS", "default").split(",") if x.strip()}
    if "default" in want or not want:
        return rows
    return [c for c in rows if c.get("name") in want]


@app.post("/v1/leads/scan")
async def leads_scan(request: Request, _: None = Depends(auth)) -> JSONResponse:  # noqa: F821
    b = await body(request)
    name = s(b, "campaign", "")
    all_camps = load_campaigns()
    multi = name in ("", "all", "default") and s(b, "areas", "") == ""
    camp: dict = {}
    if name and not multi:
        camp = next((c for c in all_camps if c.get("name") == name), {})
        if not camp:
            raise HTTPException(404, f"campaign '{name}' not found/enabled in config/campaigns/")
    if not camp:
        camp = {"name": name or "adhoc", "areas": [], "country": s(b, "country"), "city_hint": s(b, "city"),
                "niches": None, "language": s(b, "language", "auto")}
    areas = b.get("areas") or camp.get("areas") or []
    if isinstance(areas, str):
        areas = [x.strip() for x in re.split(r"[,\n]", areas) if x.strip()]
    niches = b.get("niches") or camp.get("niches") or None
    if isinstance(niches, str):
        niches = [x.strip() for x in niches.split(",") if x.strip()]
    limit = int(s(b, "limit_per_area", str(camp.get("limit_per_area", 12))) or 12)
    exclude = [x for x in re.split(r"[,\s]+", s(b, "exclude_keys")) if x]
    min_hot = int(s(b, "min_hotness", env("MIN_HOTNESS", "40")) or 0)
    try:
        if multi and all_camps:
            out = {"leads": [], "stats": {"areas": 0, "osm_raw": 0, "no_website": 0, "deduped": 0,
                                          "errors": []}, "campaigns": []}
            for c in all_camps:
                r = await RADAR.scan(c, limit_per_area=int(c.get("limit_per_area", limit) or limit),
                                     exclude_keys=exclude, min_hotness=min_hot,
                                     require_contact=s(b, "require_contact", "true") != "false")
                out["leads"].extend(r["leads"])
                for k in ("areas", "osm_raw", "no_website", "deduped"):
                    out["stats"][k] += r["stats"].get(k, 0)
                out["stats"]["errors"] += r["stats"].get("errors", [])[:2]
                out["campaigns"].append(c.get("name"))
                if len(out["leads"]) >= int(s(b, "limit", "0") or 60):
                    break
        else:
            out = await RADAR.scan(camp, areas=areas, niches=niches, limit_per_area=limit,
                                   exclude_keys=exclude, min_hotness=min_hot,
                                   require_contact=s(b, "require_contact", "true") != "false")
    except Exception as e:
        log.exception("scan failed")
        raise HTTPException(502, f"lead scan failed: {e}")
    lang_pref = camp.get("language", "auto")
    for l in out["leads"]:
        l["campaign"] = l.get("campaign") or camp.get("name", "")
        l["slug"] = slugify(l["osm_name"], l.get("area") or "")
        l["language"] = lang_pref if lang_pref in ("roman-urdu", "english") \
            else lang_for(CFG, l.get("country", ""))
        l["price_label"] = price_for(CFG, l.get("country", ""))
    out["limit_requested"] = int(s(b, "limit", "0") or 0)
    if out["limit_requested"]:
        out["leads"] = out["leads"][: out["limit_requested"]]
    out["campaign"] = camp.get("name")
    return JSONResponse(out)


# ----------------------------------------------------------------- demo sites


@app.post("/v1/demo/generate")
async def demo_generate(request: Request, _: None = Depends(auth)) -> JSONResponse:  # noqa: F821
    b = await body(request)
    name = s(b, "business_name") or s(b, "name") or s(b, "osm_name")
    if not name:
        raise HTTPException(400, "business_name is required")
    area = s(b, "area") or s(b, "city")
    lead = {
        "key": s(b, "key", f"manual/{re.sub(r'[^a-z0-9]', '', name.lower())[:24]}"),
        "osm_name": name, "city": s(b, "city") or (area.split(",")[-1].strip() if area else ""),
        "area": area, "address": s(b, "address"), "country": s(b, "country").upper(),
        "phone": re.sub(r"[^\d+]", "", s(b, "phone")), "whatsapp": re.sub(r"\D", "", s(b, "whatsapp")),
        "category": s(b, "category", "services"),
        "category_label": s(b, "category_label") or RADAR.niches.get(s(b, "category"), {}).get("label", "Local business"),
        "cta_label": RADAR.niches.get(s(b, "category"), {}).get("cta_label", "WhatsApp"),
        "brief": s(b, "brief") or RADAR.niches.get(s(b, "category"), {}).get("brief", ""),
        "extra_sections": s(b, "extra_sections") or json.dumps(
            RADAR.niches.get(s(b, "category"), {}).get("extra_sections", [])),
        "emoji": RADAR.niches.get(s(b, "category"), {}).get("emoji", "🏪"),
        "rating": s(b, "rating"), "reviews_count": s(b, "reviews_count"),
        "opening_hours": s(b, "opening_hours"), "instagram": s(b, "instagram"),
        "facebook": s(b, "facebook"), "notes": s(b, "notes"), "website": s(b, "website"),
        "slug": s(b, "slug") or slugify(name, area),
    }
    if not lead["whatsapp"] and lead["phone"]:
        lead["whatsapp"] = re.sub(r"\D", "", lead["phone"])
    lang = s(b, "language") or lang_for(CFG, lead["country"])
    res = await SITEGEN.generate(lead, "roman-urdu" if "urdu" in lang else "en")
    res["lead"] = {k: lead[k] for k in ("key", "osm_name", "slug", "city", "country", "category",
                                        "phone", "whatsapp", "hotness")}
    res["language"] = lang
    res["price_label"] = price_for(CFG, lead["country"])
    if res.get("error"):
        raise HTTPException(502, res["error"])
    return JSONResponse(res)


@app.post("/v1/site/deploy")
async def site_deploy(request: Request, _: None = Depends(auth)) -> JSONResponse:  # noqa: F821
    b = await body(request)
    html = s(b, "html")
    if not html:
        html = (b.get("html") or "") if isinstance(b.get("html"), str) else ""
    if not html:
        raise HTTPException(400, "html required")
    slug = s(b, "slug") or slugify(s(b, "business_name", "demo"), s(b, "area"))
    chk = validate(html, {"osm_name": s(b, "business_name", slug)}, CFG)
    if not chk["ok"] and s(b, "force", "false") != "true":
        raise HTTPException(422, json.dumps({"reason": "html failed checks", "issues": chk["issues"]})[:900])
    async with _lock:
        st = state()
        h = hashlib.sha256(html.encode()).hexdigest()[:16]
        if h in st.get("hashes", []):
            log.warning("duplicate design for %s (same content hash already deployed)", slug)
        st["hashes"] = ([h] + st.get("hashes", []))[:120]
        try:
            out = await DEPLOYER.deploy(slug, html, {"business": s(b, "business_name"),
                                                     "title": s(b, "title"),
                                                     "description": s(b, "meta_description")})
        except (DeployError, AiError) as e:
            raise HTTPException(502, str(e))
        st["deployed"][slug] = {"url": out["url"], "at": int(time.time()), "hash": h}
        save_state(st)
    out["checks"] = chk
    return JSONResponse(out)


@app.post("/v1/site/portfolio")
async def site_portfolio(request: Request, _: None = Depends(auth)) -> JSONResponse:  # noqa: F821
    b = await body(request)
    rows = b.get("rows") or []
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except Exception:
            rows = []
    if b.get("rows_json"):
        try:
            rows = json.loads(b["rows_json"])
        except Exception:
            pass
    out = await DEPLOYER.deploy_portfolio(portfolio_html(rows, CFG))
    out["count"] = len(rows)
    return JSONResponse(out)


# ------------------------------------------------------------------ messaging


async def llm_prompt(task: str, vars_: dict, *, max_tokens: int = 520, temperature: float = 0.8,
                     fast: bool = True) -> str:
    tpl = PROMPTS.get(task)
    if not tpl:
        raise HTTPException(500, f"prompts/{task}.md not found")
    sys_p = render(tpl, vars_)
    meta = await AI.chat([{"role": "system", "content": sys_p},
                          {"role": "user", "content": vars_.get("_user", "Write it now.")}],
                         max_tokens=max_tokens, temperature=temperature, fast=fast, tag=task)
    out = (meta.get("text") or "").strip()
    out = re.sub(r"^```.*?\n|^```$", "", out).strip()
    out = re.sub(r"^(Message|Reply|Output)\s*:\s*", "", out, flags=re.I)
    return out.strip('"\n“” ')


@app.post("/v1/outreach")
async def outreach(request: Request, _: None = Depends(auth)) -> JSONResponse:  # noqa: F821
    b = await body(request)
    country = s(b, "country").upper()
    lang = s(b, "language") or lang_for(CFG, country)
    task = "outreach-roman-urdu" if "urdu" in lang else "outreach-english"
    v = {
        "business_name": s(b, "business_name"), "category_label": s(b, "category_label", ""),
        "city": s(b, "city"), "area": s(b, "area") or s(b, "city"),
        "demo_url": s(b, "demo_url"), "price_label": s(b, "price_label") or price_for(CFG, country),
        "agency_name": CFG["AGENCY_NAME"], "owner_name": CFG["AGENCY_NAME"],
        "whatsapp_number": CFG["AGENCY_WHATSAPP"], "delivery_time_text": CFG["DELIVERY_TIME_TEXT"],
        "offer_text": CFG["OFFER_TEXT"], "gap_note": s(b, "gap_note"),
        "rating": s(b, "rating"), "reviews_count": s(b, "reviews_count"),
        "has_phone": "yes" if s(b, "phone") else "no", "tone": s(b, "tone", "friendly"),
        "notes": s(b, "notes"), "language": lang,
        "_user": f"Write the first WhatsApp message for {s(b,'business_name')}. "
                 f"Demo link: {s(b,'demo_url')}. Facts: rating={s(b,'rating') or 'unknown'}, "
                 f"area={s(b,'area') or s(b,'city')}. Remember: max 45 words, one CTA, no price.",
    }
    try:
        msg = await llm_prompt(task, v, max_tokens=220)
    except AiError as e:
        raise HTTPException(502, str(e))
    words = len(msg.split())
    if len(msg) > 520 or words > 75:                       # one tightening pass, cheap model
        v["_user"] = "Too long. Rewrite under 40 words, same facts, keep the link on its own line."
        try:
            msg2 = await llm_prompt(task, v, max_tokens=140, temperature=0.4)
            if 0 < len(msg2.split()) <= words:
                msg = msg2
        except AiError:
            pass
    if "http" not in msg and v["demo_url"]:
        msg = (msg.rstrip() + "\n" + v["demo_url"]).strip()
    phone = Radar.whatsapp_of({}, s(b, "whatsapp") or s(b, "phone"), country)
    return JSONResponse({"message": msg, "words": len(msg.split()), "chars": len(msg),
                         "language": lang, "chat_id": f"{phone}@c.us" if phone else "",
                         "subject": f"{s(b,'business_name')} — aapki website ka demo" if "urdu" in lang
                         else f"{s(b,'business_name')} — I built your website (demo)",
                         "demo_url": v["demo_url"], "has_link": "http" in msg})


@app.post("/v1/reply/classify")
async def reply_classify(request: Request, _: None = Depends(auth)) -> JSONResponse:  # noqa: F821
    b = await body(request)
    text = s(b, "message_text")
    if not text:
        raise HTTPException(400, "message_text required")
    lang = s(b, "language") or lang_for(CFG, s(b, "country"), text)
    v = {
        "language": lang, "business_name": s(b, "business_name"),
        "category_label": s(b, "category_label", "business"), "city": s(b, "city"),
        "demo_url": s(b, "demo_url"), "price_label": s(b, "price_label") or price_for(CFG, s(b, "country")),
        "delivery_time_text": CFG["DELIVERY_TIME_TEXT"], "agency_name": CFG["AGENCY_NAME"],
        "owner_name": CFG["AGENCY_NAME"], "message_text": text, "sender_name": s(b, "sender_name", "there"),
        "thread_age_hours": s(b, "thread_age_hours", "?"),
        "ai_replies_in_thread": s(b, "ai_replies_in_thread", "0"),
        "max_auto_replies": CFG["MAX_AUTO_REPLIES"],
        "last_outbound_excerpt": (s(b, "last_outbound") or "")[:300],
        "pay_methods": CFG["PAY_METHODS"], "pay_instructions": CFG["PAY_INSTRUCTIONS"],
        "offer_text": CFG["OFFER_TEXT"], "niches_hint": "",
        "_user": "Classify and, if allowed, draft the single reply. JSON only.",
    }
    keys = ["intent", "confidence", "should_reply", "reply", "summary", "next_action",
            "lead_status", "payment_info_sent", "flags"]
    try:
        obj, meta = await AI.chat_json([{"role": "system", "content": render(PROMPTS["reply-classifier"], v)},
                                        {"role": "user", "content": v["_user"]}],
                                       keys_hint=keys, max_tokens=700, temperature=0.2, tag="reply")
    except AiError as e:
        # A webhook must never hard-fail: an unparsable model reply = a human looks at it.
        log.warning("classify failed, handing to human: %s", e)
        return JSONResponse({"intent": "unclear", "confidence": 0.0, "should_reply": False, "reply": "",
                             "summary": f"AI classify failed ({type(e).__name__}) — read this one yourself",
                             "next_action": "alert_owner", "lead_status": "keep_current",
                             "payment_info_sent": False, "flags": ["model_error"], "error": str(e)[:300]})
    out: dict[str, Any] = {k: obj.get(k) for k in keys}
    out["intent"] = (out.get("intent") or "unclear").strip().lower()
    if out["intent"] not in ("interested", "price_question", "objection_price", "objection_trust",
                             "revision_request", "not_interested", "handoff_needed", "auto_reply",
                             "spam", "question_other", "unclear"):
        out["intent"] = "unclear"
    try:
        out["confidence"] = max(0.0, min(1.0, float(out.get("confidence") or 0)))
    except Exception:
        out["confidence"] = 0.0
    out["should_reply"] = str(out.get("should_reply")).lower() in ("1", "true", "yes")
    out["payment_info_sent"] = str(out.get("payment_info_sent")).lower() in ("1", "true", "yes")
    tried = int(s(b, "ai_replies_in_thread", "0") or 0)
    cap = int(CFG["MAX_AUTO_REPLIES"])
    if out["should_reply"] and tried >= cap:
        out["should_reply"] = False
        out["next_action"] = "alert_owner"
        out["guard"] = "auto-reply cap reached"
    if not env("AUTO_REPLY_ENABLED", "true").lower().startswith(("t", "1", "y")):
        out["should_reply"] = False
        out["next_action"] = out.get("next_action") or "alert_owner"
    out["reply"] = clean(out.get("reply") or "") if out["should_reply"] else ""
    out["model"], out["provider"] = meta.get("model"), meta.get("provider")
    out["quota_left"] = meta.get("quota_left")
    return JSONResponse(out)


@app.post("/v1/ai/chat")
async def ai_chat(request: Request, _: None = Depends(auth)) -> JSONResponse:  # noqa: F821
    """Escape hatch: prompt file + variables, straight from n8n — no redeploy needed to test a prompt."""
    b = await body(request)
    task = s(b, "prompt")
    vars_ = b.get("vars") or {}
    if isinstance(vars_, str):
        vars_ = json.loads(vars_ or "{}")
    if task:
        if task not in PROMPTS:
            raise HTTPException(404, f"no prompt '{task}' (have: {', '.join(sorted(PROMPTS))})")
        vars_["_user"] = s(b, "user", "Go.")
        try:
            txt = await llm_prompt(task, vars_, max_tokens=int(s(b, "max_tokens", "900")),
                                   temperature=float(s(b, "temperature", "0.8")),
                                   fast=s(b, "fast", "false") == "true")
        except AiError as e:
            raise HTTPException(502, str(e))
        return JSONResponse({"text": txt, "prompt": task})
    if not s(b, "system") and not s(b, "messages"):
        raise HTTPException(400, "either 'prompt' or 'system'/'messages' required")
    msgs = b.get("messages") or [{"role": "system", "content": s(b, "system")},
                                 {"role": "user", "content": s(b, "user")} ]
    try:
        meta = await AI.chat(msgs, max_tokens=int(s(b, "max_tokens", "2000")),
                             temperature=float(s(b, "temperature", "0.7")),
                             json_mode=s(b, "json", "false") == "true", tag="adhoc")
    except AiError as e:
        raise HTTPException(502, str(e))
    return JSONResponse({"text": meta["text"], "provider": meta["provider"], "usage": meta.get("usage")})


# ------------------------------------------------------------------- previews


@app.get("/preview/{slug}/index.html")
@app.get("/preview/{slug}/")
async def preview(slug: str):
    p = Path(DATA_DIR) / "public" / f"{slug.strip('/')}" / "index.html"
    if not p.exists() or ".." in slug:
        raise HTTPException(404, "not deployed yet (DEPLOY_TARGET=local only)")
    return HTMLResponse(p.read_text(encoding="utf-8"))


@app.get("/media/{name}")
async def media(name: str):
    from fastapi.responses import FileResponse
    f = (Path(DATA_DIR) / "media" / os.path.basename(name))
    if not f.exists():
        raise HTTPException(404, "gone")
    return FileResponse(f)


@app.exception_handler(Exception)
async def on_error(req: Request, exc: Exception):                      # pragma: no cover
    log.exception("unhandled")
    return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status_code=500)
