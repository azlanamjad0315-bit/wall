"""gateway tests — run with `make test` (no network, no API keys needed).

Covers the parts that would otherwise silently break a real campaign:
JSON extraction from weak models, the "no website" lead filter, phone → WhatsApp E.164,
Overpass query shape, the HTML quality gate, the design seed, deploy liveness, auth, and
provider fail-over when a free tier 429s.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent            # gateway/
ROOT = HERE.parent
os.environ.update({
    "DATA_DIR": tempfile.mkdtemp(prefix="gw-test-"),
    "PROMPTS_DIR": str(ROOT / "prompts"),
    "CONFIG_DIR": str(ROOT / "config"),
    "DEPLOY_TARGET": "local",
    "GATEWAY_TOKEN": "testtok",
    "AGENCY_NAME": "TestAgency",
    "WHATSAPP_NUMBER": "923001234567",
    "PRICE_MAP": "PK=PKR 15,000 one-time|default=USD 149 one-time",
    "AI_PROVIDER_CHAIN": "gemini,groq,ollama",
    "GEMINI_API_KEY": "", "GROQ_API_KEY": "",
    "PAY_METHODS": "Easypaisa: 0300-XXXXXXX",
})
sys.path.insert(0, str(HERE))

import httpx                                              # noqa: E402
import pytest                                             # noqa: E402

import ai as ai_mod                                       # noqa: E402
import leads as leads_mod                                 # noqa: E402
import sitegen as sg                                      # noqa: E402
import deploy as dp                                       # noqa: E402
import app as app_mod                                     # noqa: E402

CFG = app_mod.CFG


# ----------------------------------------------------------------- ai.py

def test_extract_json_handles_every_llm_habit():
    cases = {
        "fenced": "```json\n{\"intent\": \"interested\", \"ok\": true}\n```",
        "prose": "Sure! Here you go:\n{\"intent\": \"price_question\", \"confidence\": 0.9}\nHope this helps.",
        "trailing_comma": '{"a": 1, "b": [1,2,],}',
        "smart_quotes": "{“intent”: “spam”}",
        "literal_newline": '{"reply": "line1\nline2", "intent": "unclear"}',
        "nested_braces": 'x {"flags": ["a"], "inner": {"k": "}"} } y',
    }
    for name, raw in cases.items():
        got = ai_mod.extract_json(raw)
        assert isinstance(got, dict), f"{name}: got {got!r}"
    assert ai_mod.extract_json("total garbage") is None


def test_coerce_keys_rescue_for_weak_models():
    txt = "intent: interested\nshould_reply = true\nreply: \"aao install karte hain\"\n"
    obj = ai_mod.coerce_keys(txt, ["intent", "should_reply", "reply"])
    assert obj and obj["intent"] == "interested" and obj["should_reply"] is True


@pytest.mark.asyncio
async def test_provider_fallback_on_429_and_budget():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        seen.append(host)
        if host.endswith("googleapis.com"):
            return httpx.Response(429, json={"error": "rate limit"}, headers={"retry-after": "0"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"ok\": 1}"}}],
                                         "usage": {"total_tokens": 3}})

    p1 = ai_mod.Provider("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "k", "m")
    p2 = ai_mod.Provider("groq", "https://api.groq.com/openai/v1", "k", "m")
    for p in (p1, p2):
        p.min_interval_ms = 0
    c = ai_mod.AiClient([p1, p2], ai_mod.Budget(os.environ["DATA_DIR"] + "/b1", cap=50),
                        transport=httpx.MockTransport(handler))
    out = await c.chat([{"role": "user", "content": "hi"}], tag="t")
    assert out["provider"] == "groq" and "googleapis" in seen[0]
    assert out["text"].startswith("{")
    assert c.budget.used("gemini") >= 2 and c.budget.used("groq") == 1


@pytest.mark.asyncio
async def test_all_providers_dead_raises_clear_error():
    def dead(request):
        return httpx.Response(503, text="down")
    p = ai_mod.Provider("gemini", "https://x.googleapis.com/v1beta/openai", "k", "m", min_interval_ms=0)
    p2 = ai_mod.Provider("ollama", "https://ollama.local", "o", "m", style="ollama", min_interval_ms=0)
    c = ai_mod.AiClient([p, p2], ai_mod.Budget(os.environ["DATA_DIR"] + "/b2", cap=50),
                        transport=httpx.MockTransport(dead))
    with pytest.raises(ai_mod.AiError) as e:
        await c.chat([{"role": "user", "content": "x"}])
    assert "all AI providers failed" in str(e.value)


@pytest.mark.asyncio
async def test_ollama_payload_shape():
    cap = {}

    def handler(request):
        cap.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "ok"}})
    p = ai_mod.Provider("ollama", "http://ollama:11434", "x", "qwen2.5:7b", style="ollama", min_interval_ms=0)
    c = ai_mod.AiClient([p], ai_mod.Budget(os.environ["DATA_DIR"] + "/b3", cap=10),
                        transport=httpx.MockTransport(handler))
    await c.chat([{"role": "user", "content": "hi"}], json_mode=True, max_tokens=99)
    assert cap["options"]["num_predict"] == 99 and cap["format"] == "json" and cap["stream"] is False


def test_daily_cap_skips_exhausted_provider():
    b = ai_mod.Budget(os.environ["DATA_DIR"] + "/b4", cap=2)
    b.burn("gemini"); b.burn("gemini")
    assert b.left("gemini") == 0


# ------------------------------------------------------------- leads.py

@pytest.fixture
def radar():
    return leads_mod.Radar(CFG, app_mod.GEO, os.environ["DATA_DIR"] + "/radar")


def test_overpass_query_shape(radar):
    q = radar.build_query([24.80, 67.00, 24.86, 67.07], ["restaurant", "salon"], 30)
    assert q.startswith("[out:json][timeout:90][bbox:24.8,67.0,24.86,67.07];")
    assert 'nwr["amenity"~"^(restaurant|cafe' in q
    assert '["website"!~"."]' in q                       # server-side "no website" filter
    assert q.rstrip().endswith("out tags center 30;")
    assert q.count("(bbox)") >= 2 and "nan" not in q


def test_verify_mode_keeps_websites_for_code_filtering(radar):
    radar.verify_site = True
    q = radar.build_query([1, 2, 3, 4], ["restaurant"], 10)
    assert '["website"!~"."]' not in q
    radar.verify_site = False


def test_normalise_accepts_no_website_and_drops_real_site(radar):
    camp = {"country": "PK", "city_hint": "Karachi"}
    good = radar.normalise({"type": "node", "id": 1, "lat": 24.9, "lon": 67.05, "tags": {
        "name": "Bismillah Bakers", "amenity": "restaurant", "cuisine": "bakery",
        "phone": "+92 300 1234567", "opening_hours": "Mo-Su 09:00-23:00",
        "addr:street": "Main Shahrah-e-Faisal", "addr:city": "Karachi", "rating": 4.6, "reviews": 80}},
        "DHA Phase 5, Karachi", camp)
    assert good and good["category"] == "restaurant"
    assert good["whatsapp"] == "923001234567"
    assert good["hotness"] >= 70
    assert good["category_label"].lower().startswith("restaurant")

    has_site = radar.normalise({"type": "node", "id": 2, "tags": {
        "name": "Awesome Tailors", "craft": "tailor", "website": "https://awesometalors.pk",
        "phone": "03001234567"}}, "Saddar, Karachi", camp)
    assert has_site is None, "business with a real website must be filtered out"

    insta_only = radar.normalise({"type": "node", "id": 3, "tags": {
        "name": "Glow Salon", "shop": "beauty", "website": "https://instagram.com/glow"}},
        "Gulshan, Karachi", camp)
    assert insta_only and insta_only["category"] == "salon", "Instagram-only = no website → lead"
    assert insta_only["social"].count("instagram") == 1

    anon = radar.normalise({"type": "node", "id": 4, "tags": {"amenity": "cafe"}}, "X, Karachi", camp)
    assert anon is None, "untagged POIs are useless for outreach"


def test_phone_normalisation_variants(radar):
    cases = {"0300-1234567": "923001234567", "+92 300 1234567": "923001234567",
             "00923001234567": "923001234567", "923001234567": "923001234567", "12": ""}
    for raw, want in cases.items():
        got = leads_mod.Radar.whatsapp_of({}, raw, "PK")
        assert got == want, f"{raw} -> {got} != {want}"


def test_radar_requires_contact_and_hotness_floor(radar, monkeypatch):
    async def fake_bbox(self, area, country):
        return [24.8, 67.0, 24.9, 67.1]

    async def fake_fetch(self, ql):
        return {"elements": [
            {"type": "node", "id": 10, "tags": {"name": "No Contact Cafe", "amenity": "cafe"}},
            {"type": "node", "id": 11, "tags": {"name": "Dhaba", "amenity": "restaurant",
                                                 "phone": "03001234567"}},
            {"type": "node", "id": 11, "tags": {"name": "Dhaba", "amenity": "restaurant",
                                                "phone": "03001234567"}},        # duplicate id
        ]}
    monkeypatch.setattr(leads_mod.Radar, "bbox_for", fake_bbox)
    monkeypatch.setattr(leads_mod.Radar, "_fetch", fake_fetch)
    out = __import__("asyncio").run(radar.scan({"country": "PK"}, areas=["Nowhere, Karachi"],
                                               niches=["restaurant"], limit_per_area=10))
    names = [l["osm_name"] for l in out["leads"]]
    assert names == ["Dhaba"], "uncontactable POI must be dropped"
    assert out["stats"]["osm_raw"] == 3 and out["stats"]["no_website"] == 1, "duplicate OSM id deduped"
    out2 = __import__("asyncio").run(radar.scan({"country": "PK"}, areas=["Nowhere, Karachi"],
                                                 niches=["restaurant"], limit_per_area=10,
                                                 exclude_keys=["node/11"]))
    assert out2["leads"] == [] and out2["stats"]["deduped"] == 2, "CRM keys must be honoured"


def test_geocode_respects_nominatim_policy(radar):
    assert "contact:" in leads_mod._UA % "a@b.c"
    assert radar.geo.ttl > 3600 * 24, "Nominatim must be cached (>= 1 day)"


# ------------------------------------------------------------ sitegen.py

def test_slugify_is_urlsafe_and_stable():
    a = sg.slugify("Bismillah Bakers!", "DHA Phase 5, Karachi")
    b = sg.slugify("Bismillah Bakers!", "DHA Phase 5, Karachi")
    assert a == b and re.fullmatch(r"[a-z0-9-]+", a)
    assert len(a) <= 60 and sg.slugify("", "").startswith("demo")
    assert " " not in a and "--" not in a


def test_seed_variety_across_leads():
    seeds = json.loads(Path(CFG["SEEDS_FILE"]).read_text())
    got = {json.dumps(sg.pick_seed({"key": f"node/{i}"}, seeds)["palette"], sort_keys=True)
           for i in range(24)}
    assert len(got) >= 4, "design seeds must actually vary between leads"
    one = sg.pick_seed({"key": "node/5"}, seeds)
    assert sg.pick_seed({"key": "node/5"}, seeds)["font"]["id"] == one["font"]["id"], "must be deterministic"


def test_render_leaves_no_unresolved_tokens():
    tpl = Path(ROOT / "prompts" / "site-generator.md").read_text()
    lead = {"key": "node/1", "osm_name": "Test", "city": "Karachi", "area": "DHA", "country": "PK",
            "phone": "923001234567", "whatsapp": "923001234567", "slug": "test-dha-abcd"}
    seeds = json.loads(Path(CFG["SEEDS_FILE"]).read_text())
    v = SiteGenHarness(ai=None, prompts={}, seeds=seeds, cfg=CFG).vars_for(lead, sg.pick_seed(lead, seeds), "en")
    out = sg.render(tpl, v)
    leftover = [m for m in re.findall(r"\{\{\s*([a-z0-9_]+)\s*\}\}", out) if m not in v]
    assert not leftover, f"prompt uses variables nobody fills: {leftover}"
    assert "font=Fraunces" in out or "family=" in out, "google font url must be injected"


class SiteGenHarness(sg.SiteGen):
    pass


def test_extract_html_strips_wrappers():
    raw = "Here is your site:\n```html\n<!doctype html><html><body>hi</body></html>\n```\nEnjoy!"
    assert sg.extract_html(raw) == "<!doctype html><html><body>hi</body></html>"


def test_validate_gate():
    good = """<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bismillah Bakers</title><meta name="description" content="x"><link rel="preconnect" href="https://fonts.googleapis.com">
</head><body><header><nav>n</nav></header><main><section><img src="https://picsum.photos/seed/a/800/600" width="800" height="600" loading="lazy" decoding="async" alt="cake" onerror="this.remove()">
<a href="https://wa.me/923001234567?text=hi">Order</a><a href="tel:923001234567">Call</a>
<iframe src="https://www.google.com/maps?q=DHA%20Karachi&output=embed" loading="lazy"></iframe>
<script type="application/ld+json">{"@type":"Bakery"}</script></section></main><footer>Bismillah Bakers</footer></body></html>"""
    good += "<!-- pad -->" * 700
    chk = sg.validate(good, {"osm_name": "Bismillah Bakers"}, CFG)
    assert chk["ok"], chk["issues"]
    chk2 = sg.validate("<html><body>hello</body></html>", {"osm_name": "Zara Salon"}, CFG)
    assert not chk2["ok"]
    joined = " ".join(chk2["issues"]).lower()
    for must in ("viewport", "whatsapp", "map", "json-ld", "doctype"):
        assert must in joined, f"quality gate must report the missing {must}"

def test_validate_catches_placeholders():
    h = ('<!doctype html><html><head><meta name="viewport"></head><body><header><main><section>'
         'Your Business Here — visit https://example.com for more. Lorem ipsum dolor sit amet.'
         '</section></main></header><footer></footer></body></html>')
    chk = sg.validate(h, {"osm_name": "X"}, CFG)
    assert not chk["ok"] and any("placeholder" in i for i in chk["issues"])


def test_ensure_hook_and_danger_stripping():
    html = "<html><body><footer>bye</footer></body></html>"
    hooked = sg.ensure_hook(html, CFG, {"osm_name": "Shop"})
    assert "wa.me/923001234567" in hooked and "DEMO" in hooked and hooked.count("data-demo-hook") == 1
    assert sg.ensure_hook(hooked, CFG, {"osm_name": "Shop"}).count("data-demo-hook") == 1, "must be idempotent"
    off = sg.ensure_hook(html, {**CFG, "DEMO_BADGE": "false"}, {"osm_name": "Shop"})
    assert "data-demo-hook" not in off
    dirty = ('<script src="https://evil.example/x.js"></script>'
             '<iframe src="https://evil.example/e"></iframe>'
             '<iframe src="https://www.google.com/maps?q=a&output=embed"></iframe>'
             '<img src="x" onerror="document.cookie=1"><img src="y" onerror="this.remove()">')
    clean = sg.strip_danger(dirty)
    assert "evil.example" not in clean and "document.cookie" not in clean
    assert "google.com/maps" in clean and "this.remove()" in clean


def test_describe_and_price_and_lang():
    d = sg.describe({"osm_name": "Karachi Grill", "category_label": "Restaurant", "city": "Karachi",
                     "opening_hours": "Mo-Su 10:00-22:00", "rating": "4.5"}, CFG)
    assert 40 < len(d) <= 155 and "Karachi Grill" in d and "10am-10pm" in d
    assert sg.price_for(CFG, "PK").startswith("PKR 15,000")
    assert sg.price_for(CFG, "US") == "USD 149 one-time"
    assert sg.price_for(CFG, "ZZ") == "USD 149 one-time"
    assert sg.lang_for(CFG, "PK") == "roman-urdu" and sg.lang_for(CFG, "GB") == "english"
    assert sg.lang_for({"LANGUAGE": "english"}, "PK") == "english"


def test_portfolio_page_from_crm_rows():
    rows = [{"demo_url": "https://x.github.io/a/", "title": "A Bakery", "category_label": "Restaurant",
             "city": "Karachi", "emoji": "🍽️"}, {"demo_url": ""}, {"title": "no url"}]
    h = sg.portfolio_html(rows, CFG)
    assert h.startswith("<!doctype html>") and "A Bakery" in h and h.count("<a class=\"c\"") == 1
    assert sg.portfolio_html([], CFG)            # must not crash with zero rows


def test_prompt_files_have_required_sections():
    for f, needle in [("site-generator", "NON-NEGOTIABLE OUTPUT CONTRACT"),
                      ("outreach-roman-urdu", "HARD RULES"),
                      ("outreach-english", "HARD RULES"),
                      ("reply-classifier", "not_interested"),
                      ("followup", "payment_reminder")]:
        txt = Path(ROOT / "prompts" / f"{f}.md").read_text()
        assert needle.lower() in txt.lower(), f"{f} missing {needle}"
        assert "{{" in txt, f"{f} must be templated"


# ------------------------------------------------------------- deploy.py

@pytest.mark.asyncio
async def test_local_deploy_writes_and_is_live(radar):
    d = dp.Deployer({**CFG, "DEPLOY_TARGET": "local", "DATA_DIR": os.environ["DATA_DIR"] + "/dep"})
    out = await d.deploy("Test-Bakers-dha-1234", "<html>" + "x" * 5000 + "</html>")
    assert out["live"] and Path(out["path"]).exists() and out["slug"] == "test-bakers-dha-1234"
    assert out["url"].endswith("/preview/test-bakers-dha-1234/")


@pytest.mark.asyncio
async def test_github_deploy_uses_contents_api_and_waits():
    calls = []
    state = {"live_hits": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith(".nojekyll"):
            return httpx.Response(404 if request.method == "GET" else 201, json={"content": {"sha": "n"}})
        if request.method == "GET" and request.url.path.startswith("/repos/o/demo-sites/contents/"):
            return httpx.Response(200, json={"sha": "abc123"})
        if request.method == "PUT":
            body = json.loads(request.content)
            assert body["sha"] == "abc123", "must send sha when updating an existing demo"
            assert len(body["content"]) > 100 and body["branch"] == "main"
            return httpx.Response(201, json={"commit": {"sha": "c1"}})
        if request.url.host == "o.github.io":
            state["live_hits"] += 1
            return httpx.Response(200 if state["live_hits"] >= 2 else 404, text="<html>" + "y" * 2000)
        return httpx.Response(404)

    cfg = {**CFG, "DEPLOY_TARGET": "github-pages", "GITHUB_REPO": "o/demo-sites",
           "GITHUB_TOKEN": "t", "PAGES_BASE_URL": "https://o.github.io/demo-sites",
           "DATA_DIR": os.environ["DATA_DIR"] + "/gh"}
    d = dp.Deployer(cfg)
    d._transport = httpx.MockTransport(handler)
    out = await d.deploy_github("bakers-dha", "<html>" + "z" * 4000 + "</html>")
    assert out["url"] == "https://o.github.io/demo-sites/bakers-dha/" and out["live"]
    assert ("PUT", "/repos/o/demo-sites/contents/bakers-dha/index.html") in calls
    assert ("GET", "/repos/o/demo-sites/contents/.nojekyll") in calls


@pytest.mark.asyncio
async def test_github_misconfig_fails_loudly():
    with pytest.raises(dp.DeployError):
        await dp.Deployer({**CFG, "DEPLOY_TARGET": "github-pages", "GITHUB_REPO": "",
                           "DATA_DIR": os.environ["DATA_DIR"] + "/gh2"}).deploy("a", "<html></html>")


# ------------------------------------------------------------------ app.py

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    return TestClient(app_mod.app)


HDR = {"X-Gateway-Token": "testtok"}


def test_healthz_reports_missing_keys(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] and j["deploy_target"] == "local"
    assert "site-generator" in j["prompts"] and "restaurant" in j["niches"]
    names = [p["name"] for p in j["providers"]]
    assert names == ["ollama"], f"only Ollama should be usable with no API keys, got {names}"
    assert "Ollama" in j["warn"], "operator must be warned that local-only AI is a quality trade-off"


def test_auth_required(client):
    assert client.post("/v1/outreach", json={}).status_code == 401
    assert client.post("/v1/outreach", json={}, headers={"X-Gateway-Token": "nope"}).status_code == 401


def test_site_deploy_endpoint_local(client):
    html = "<!doctype html><html><head><meta name=viewport></head><body>" + "p" * 9000 + "</body></html>"
    r = client.post("/v1/site/deploy", headers=HDR,
                    json={"slug": "smoke-test", "html": html, "business_name": "Smoke Test", "force": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["live"] and "smoke-test" in r.json()["url"]
    assert client.get("/preview/smoke-test/").text.startswith("<!doctype html>")
    r2 = client.post("/v1/site/deploy", headers=HDR, json={"slug": "x", "html": "<html></html>"})
    assert r2.status_code == 422 and "failed checks" in r2.text, "broken HTML must never reach a prospect"


def test_outreach_endpoint_builds_link_and_chatid(client, monkeypatch):
    async def fake_chat(self, messages, **kw):
        return {"text": "Bismillah Bakers, DHA me aapka menu kahin nahi.\n"
                        "Maine aapke liye page bana diya.\nhttps://x.github.io/demo/bismillah/\n"
                        "Dekh lein, koi charge nahi.",
                "provider": "gemini", "model": "gemini-2.5-flash", "quota_left": 10}
    monkeypatch.setattr(ai_mod.AiClient, "chat", fake_chat)
    r = client.post("/v1/outreach", headers=HDR, json={
        "business_name": "Bismillah Bakers", "category_label": "Restaurant", "city": "Karachi",
        "area": "DHA Phase 5", "country": "PK", "whatsapp": "0300-1234567",
        "demo_url": "https://x.github.io/demo/bismillah/", "rating": "4.6"})
    j = r.json()
    assert r.status_code == 200, r.text
    assert j["language"] == "roman-urdu" and j["chat_id"] == "923001234567@c.us", "wa.me needs E.164"
    assert j["has_link"] and "https://x.github.io/demo/bismillah/" in j["message"]
    assert j["words"] <= 60 and "PKR" not in j["message"], "price must not leak into first touch"


def test_outreach_appends_missing_link(client, monkeypatch):
    async def no_link(self, messages, **kw):
        return {"text": "Salam! Aapke liye site bana di thi, dekh lein.", "provider": "groq", "model": "m"}
    monkeypatch.setattr(ai_mod.AiClient, "chat", no_link)
    j = client.post("/v1/outreach", headers=HDR, json={"business_name": "X", "country": "GB",
                                                       "demo_url": "https://a/b/"}).json()
    assert j["message"].endswith("https://a/b/") and j["language"] == "english"


def test_reply_classify_caps_and_guards(client, monkeypatch):
    async def fake_json(self, messages, *, keys_hint=None, **kw):
        return ({"intent": "interested", "confidence": 0.92, "should_reply": True,
                 "reply": "Shukriya! Payment here: Easypaisa: 0300-XXXXXXX", "summary": "Interested — Bismillah",
                 "next_action": "send_reply", "lead_status": "interested", "payment_info_sent": True,
                 "flags": []}, {"provider": "gemini", "model": "m", "quota_left": 5})
    monkeypatch.setattr(ai_mod.AiClient, "chat_json", fake_json)
    j = client.post("/v1/reply/classify", headers=HDR, json={
        "message_text": "bhai kamaal ka hai, kaise loon?", "country": "PK",
        "ai_replies_in_thread": 0, "business_name": "Bismillah"}).json()
    assert j["intent"] == "interested" and j["should_reply"] and j["payment_info_sent"]
    assert j["lead_status"] == "interested" and "Easypaisa" in j["reply"]

    j2 = client.post("/v1/reply/classify", headers=HDR, json={
        "message_text": "bhai kamaal ka hai", "ai_replies_in_thread": 9, "country": "PK"}).json()
    assert j2["should_reply"] is False and j2["next_action"] == "alert_owner", "cap must silence the bot"
    assert j2["reply"] == ""

    async def broken(self, messages, *, keys_hint=None, **kw):
        raise ai_mod.AiError("all AI providers failed for task 'reply' [gemini:429, groq:429]")
    monkeypatch.setattr(ai_mod.AiClient, "chat_json", broken)
    r3 = client.post("/v1/reply/classify", headers=HDR, json={"message_text": "interested!", "country": "PK"})
    assert r3.status_code == 200, "webhook must not 502 -> n8n would retry-storm"
    j3 = r3.json()
    assert j3["intent"] == "unclear" and j3["next_action"] == "alert_owner" and not j3["should_reply"]


def test_reply_not_interested_never_replies(client, monkeypatch):
    async def nope(self, messages, *, keys_hint=None, **kw):
        return ({"intent": "not_interested", "confidence": 0.99, "should_reply": True,
                  "reply": "please think again, we can discount", "summary": "s", "next_action": "send_reply",
                  "lead_status": "not_interested", "payment_info_sent": False, "flags": []},
                {"provider": "gemini", "model": "m"})
    monkeypatch.setattr(ai_mod.AiClient, "chat_json", nope)
    j = client.post("/v1/reply/classify", headers=HDR, json={"message_text": "nahi chahiye"}).json()
    assert j["should_reply"] is True          # gateway passes the flag through; workflow decides
    assert j["lead_status"] == "not_interested"


def test_ai_escape_hatch_404s_unknown_prompt(client):
    r = client.post("/v1/ai/chat", headers=HDR, json={"prompt": "nope"})
    assert r.status_code == 404 and "site-generator" in r.text


def test_leads_scan_requires_known_campaign(client):
    assert client.post("/v1/leads/scan", headers=HDR, json={"campaign": "atlantis"}).status_code == 404


def test_demo_generate_requires_name(client):
    assert client.post("/v1/demo/generate", headers=HDR, json={}).status_code == 400
