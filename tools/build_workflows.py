#!/usr/bin/env python3
"""build_workflows.py — generates the import-ready n8n workflow JSON files.

Why a generator instead of hand-written JSON: n8n's format is picky (node ids must be unique,
connections are keyed by node NAME, every branch needs a matching output index) and a single
typo means "import failed" with a useless error. Here the graph is described in Python, and
tools/validate_workflows.py checks shape + expression balance before anything is written.

    python3 tools/build_workflows.py            # writes n8n/workflows/*.json
    python3 tools/validate_workflows.py         # re-checks the written files

Workflows produced
  00-crm-bridge      one place where CRM (Baserow/Sheets) reads+writes happen
  01-leads-and-demos daily radar -> AI demo site -> deploy -> CRM
  02-outreach        queue -> AI Roman-Urdu/English message -> WhatsApp/email, rate limited
  03-replies         WAHA webhook -> AI classify -> auto-reply / Telegram alert / close
  04-followup-report follow-up ladder + portfolio page + nightly Telegram report
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "n8n" / "workflows"

HTTP, CODE, IF, SW, SIB, WAIT, SCHED, HOOK, RESPOND = (
    "n8n-nodes-base.httpRequest", "n8n-nodes-base.code", "n8n-nodes-base.if", "n8n-nodes-base.switch",
    "n8n-nodes-base.splitInBatches", "n8n-nodes-base.wait", "n8n-nodes-base.scheduleTrigger",
    "n8n-nodes-base.webhook", "n8n-nodes-base.respondToWebhook")


def x(expr: str) -> str:
    """mark a string as an n8n expression (idempotent: '={{a}}' -> '={{a}}')"""
    e = str(expr)
    return e if e.startswith("=") else "=" + e


class WF:
    def __init__(self, name: str, doc: str):
        self.name, self.doc = name, doc
        self.nodes: list[dict] = []
        self.edges: list[tuple[str, str, int, int]] = []   # (from, to, from_out, to_in)
        self.by_name: dict[str, dict] = {}

    # -- graph ------------------------------------------------------------
    def add(self, label: str, type_: str, tv: float, params: dict, *, notes: str = "",
            onError: str | None = None, retry: int = 0, always: bool = False,
            disabled: bool = False, credentials: dict | None = None) -> str:
        n = {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.name}:{label}")),
            "name": label, "type": type_, "typeVersion": tv, "position": [0, 0],
            "parameters": params,
        }
        if notes:
            n["notesInFlow"] = True
            n["notes"] = notes
        if onError:
            n["onError"] = onError
        if retry:
            n.update({"retryOnFail": True, "maxTries": retry, "waitBetweenTries": 5000})
        if always:
            n["alwaysOutputData"] = True
        if disabled:
            n["disabled"] = True
        if credentials:
            n["credentials"] = credentials
        self.nodes.append(n)
        self.by_name[label] = n
        return label

    def link(self, a: str, b: str, out: int = 0, inp: int = 0) -> None:
        self.edges.append((a, b, out, inp))

    def chain(self, *labels: str) -> None:
        for a, b in zip(labels, labels[1:]):
            self.link(a, b)

    # -- layout (BFS depth = column, order = row) -------------------------
    def layout(self) -> None:
        """Auto-place nodes on the canvas: column = distance from a trigger, row = slot in that column.
        Graph edges here contain loops (SplitInBatches), so this is a single-pass BFS with a
        visited set — never re-queue a node or you get an infinite cycle."""
        kids: dict[str, list[str]] = {}
        parents: set[str] = set()
        for a, b, _o, _i in self.edges:
            kids.setdefault(a, []).append(b)
            parents.add(b)
        roots = [n["name"] for n in self.nodes if n["name"] not in parents] or [self.nodes[0]["name"]]
        depth: dict[str, int] = {}
        q: deque[list[str]] = deque()
        for r in roots:
            depth[r] = 0
            q.append([r])
        while q:
            cur = q.popleft()
            for node in cur:
                for k in kids.get(node, []):
                    if k in depth:
                        continue
                    depth[k] = depth[node] + 1
                    q.append([k])
        for n in self.nodes:                       # orphans (unused notes nodes) sit on the right
            depth.setdefault(n["name"], (max(depth.values()) + 1) if depth else 0)
        rows: dict[int, int] = {}
        for n in self.nodes:
            d = depth[n["name"]]
            r = rows.get(d, 0)
            rows[d] = r + 1
            n["position"] = [120 + d * 260, 40 + r * 200]

    def json(self) -> dict:
        self.layout()
        conns: dict[str, dict] = {}
        for a, b, o, i in self.edges:
            conns.setdefault(a, {}).setdefault("main", [])
            lst = conns[a]["main"]
            while len(lst) <= o:
                lst.append([])
            lst[o].append({"node": b, "type": "main", "index": i})
        return {"name": self.name, "nodes": self.nodes, "connections": conns,
                "active": False, "pinData": {}, "settings": {"executionOrder": "v1",
                                                              "saveExecutionProgress": True,
                                                              "timezone": "Asia/Karachi"},
                "meta": {"templateCredsSetupCompleted": False}, "versionId": str(uuid.uuid4())}

    def write(self) -> Path:
        OUT.mkdir(parents=True, exist_ok=True)
        p = OUT / f"{self.name}.json"
        p.write_text(json.dumps(self.json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return p


# ------------------------------------------------------------------ node sugar


def http(wf: WF, label: str, url: str, *, method: str = "GET", headers: dict | None = None,
         body: dict | None = None, query: dict | None = None, notes: str = "", raw: bool = False,
         timeout: int = 900_000, onError: str | None = "continueRegularOutput", retry: int = 0,
         always: bool = False, ctype: str = "json", disabled: bool = False) -> str:
    p: dict = {"url": url if url.startswith("=") else x(url), "options": {"timeout": timeout}}
    if method != "GET":
        p["method"] = method
    if headers:
        p["sendHeaders"] = True
        p["headerParameters"] = {"parameters": [{"name": k, "value": v} for k, v in headers.items()]}
    if query:
        p["sendQuery"] = True
        p["queryParameters"] = {"parameters": [{"name": k, "value": v} for k, v in query.items()]}
    if body is not None:
        p["sendBody"] = True
        if isinstance(body, str):
            p["specifyBody"] = "json"
            p["jsonBody"] = body
        else:
            p["specifyBody"] = "keypair"
            p["bodyParameters"] = {"parameters": [{"name": k, "value": v} for k, v in body.items()]}
    p["options"]["response"] = {"neverError": True, "responseFormat": "text" if raw else ctype}
    return wf.add(label, HTTP, 4.2, p, notes=notes, onError=onError, retry=retry, always=always,
                  disabled=disabled)


def gw(wf: WF, label: str, path: str, body: dict, *, notes: str = "", retry: int = 2,
       onError: str | None = "continueErrorOutput", always: bool = False) -> str:
    """call the ai-gateway service (docker network)"""
    return http(wf, label, x("{{ $env.GATEWAY_URL + '" + path + "' }}"), method="POST",
                headers={"Content-Type": "application/json",
                         "X-Gateway-Token": x("{{ $env.GATEWAY_TOKEN }}")},
                body=body, notes=notes, retry=retry, onError=onError, always=always)


def code(wf: WF, label: str, js: str, *, notes: str = "", always: bool = False,
         run_every: bool = False) -> str:
    """Code nodes run once for all items (that is what the dedupe/queue maths expects)."""
    n = wf.add(label, CODE, 2, {"jsCode": js.strip(), "mode": "runOnceForAllItems"},
               notes=notes, always=always)
    if not run_every:
        wf.by_name[n]["executeOnce"] = True
    return n


def ifs(wf: WF, label: str, js_cond: str, *, notes: str = "", negate: bool = False) -> str:
    return wf.add(label, IF, 2.2, {
        "conditions": {
            "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
            "combinator": "and",
            "conditions": [{"id": str(uuid.uuid4()), "leftValue": x(js_cond),
                            "rightValue": "", "operator": {"type": "boolean", "operation": "false"
                                                           if negate else "true"}}],
        }, "options": {}}, notes=notes)


def sw(wf: WF, label: str, on: str, outputs: list[str], *, fallback: bool = True,
        notes: str = "") -> str:
    vals = []
    for o in outputs:
        vals.append({
            "conditions": {
                "options": {"caseSensitive": False, "leftValue": "", "typeValidation": "loose",
                            "version": 2},
                "combinator": "and",
                "conditions": [{"id": str(uuid.uuid4()), "leftValue": x(on), "rightValue": o,
                                "operator": {"type": "string", "operation": "equals"}}],
            }, "renameOutput": True, "outputKey": o})
    p = {"rules": {"values": vals}, "options": ({"fallbackOutput": "extra"} if fallback else {})}
    return wf.add(label, SW, 3.2, p, notes=notes)


def sched(wf: WF, label: str, expr_env: str, *, notes: str = "") -> str:
    return wf.add(label, SCHED, 1.2, {"rule": {"interval": [{"field": "cronExpression",
                                                             "expression": x("{{ $env." + expr_env +
                                                                             " }}")}]}}, notes=notes)


def sib(wf: WF, label: str = "Per item (loop)", size: int = 1) -> str:
    return wf.add(label, SIB, 3, {"options": {"batchSize": size, "reset": False}})


def wait(wf: WF, label: str, seconds, *, notes: str = "") -> str:
    """seconds may be a number, an expression string, or an x()-wrapped expression."""
    amt = x(seconds) if isinstance(seconds, str) and (seconds.lstrip("=").startswith("{{")) else seconds
    return wf.add(label, WAIT, 1.1, {"amount": amt, "unit": "seconds"}, notes=notes,
                  onError="continueRegularOutput")


def crm_list(wf: WF, label: str = "Read CRM", *, after: str | None = None, after_out: int = 0,
              notes: str = "") -> str:
    """One Baserow read -> ONE item: {rows:[...], keys:'a,b', count, today}.
    Deliberately not a per-row fan-out: 500 leads = 1 item = fast, no pagination bugs."""
    raw = http(wf, label + " · raw", x("{{ $env.BASEROW_URL + '/api/database/rows/table/' + "
                                       "$env.BASEROW_TABLE_ID + '/' }}"), method="GET",
               query={"user_field_names": "true", "limit": "100000"},
               headers={"Authorization": x("{{ 'Token ' + $env.BASEROW_TOKEN }}")},
               onError="continueRegularOutput", retry=2, always=True,
               notes=notes or "Baserow (self-hosted, free). Swap to Google Sheets: docs/crm.md")
    idx = code(wf, label, """
// Baserow row -> flat lead object + a few derived helpers every workflow needs
const src = $input.first().json || {};
const rows = Array.isArray(src.results) ? src.results : [];
const today = new Date().toISOString().slice(0, 10);
const clean = rows.map(r => {
  const f = {};
  for (const [k, v] of Object.entries(r)) {
    if (v === null || v === undefined) { f[k] = ''; continue; }
    f[k] = (typeof v === 'object' && !Array.isArray(v)) ? (v.text ?? v.value ?? JSON.stringify(v)) : v;
  }
  f.row_id = r.id ?? '';
  const d = s => (s ? Math.floor((Date.now() - new Date(String(s).slice(0, 10)).getTime()) / 864e5) : 9999);
  f._days_since_out = d(f.sent_at);
  f._days_since_in = d(f.last_inbound);
  f._contacted_today = String(f.sent_at || '').startsWith(today);
  return f;
});
// 'keys' is what scan must NOT return again. Retryable statuses are deliberately left out,
// so yesterday's demo_failed / stalled leads get one more shot today.
const RETRYABLE = ['demo_failed', 'scanned', 'stalled', 'deploy_pending'];
return [{ json: { rows: clean,
                  keys: clean.filter(r => RETRYABLE.indexOf(String(r.status || '')) < 0)
                        .map(r => r.lead_key).filter(Boolean).join(','),
                  retryable: clean.filter(r => RETRYABLE.indexOf(String(r.status || '')) >= 0).length,
                  count: clean.length, today, empty: clean.length === 0 } }];
""", always=True)
    wf.chain(raw, idx)
    if after:
        wf.link(after, raw, after_out)
    return label


def crm_upsert(wf: WF, label: str, *, after: str, notes: str = "") -> list[str]:
    """Input: an item whose json is the field map (row_id present -> PATCH, else POST).
    Returns the two exit node names; both should be wired to whatever continues the flow."""
    body = code(wf, label + " · body", """
// drop control keys, stringify objects, keep numbers as numbers -> Baserow user_field_names API
const j = $input.first().json || {};
const skip = new Set(['row_id', 'op', 'target', 'empty', 'skip', 'why']);
const b = {};
for (const [k, v] of Object.entries(j)) {
  if (skip.has(k) || v === undefined || v === null) continue;
  b[k] = (typeof v === 'object') ? JSON.stringify(v).slice(0, 1500) : v;
}
return [{ json: { row_id: j.row_id ? String(j.row_id) : '', body_json: JSON.stringify(b),
                  field_count: Object.keys(b).length } }];
""", notes=notes or "upsert: PATCH if row_id, else POST", always=True)
    ex = ifs(wf, label + " · exists?", "{{ Boolean($json.row_id) }}")
    upd = http(wf, label + " · update", x("{{ $env.BASEROW_URL + '/api/database/rows/table/' + "
                                          "$env.BASEROW_TABLE_ID + '/' + $json.row_id + '/?user_field_names=true' }}"),
               method="PATCH", headers={"Authorization": x("{{ 'Token ' + $env.BASEROW_TOKEN }}")},
               body=x('{{ $json.body_json }}'), onError="continueRegularOutput", retry=2, always=True)
    new = http(wf, label + " · create", x("{{ $env.BASEROW_URL + '/api/database/rows/table/' + "
                                          "$env.BASEROW_TABLE_ID + '/?user_field_names=true' }}"),
               method="POST", headers={"Authorization": x("{{ 'Token ' + $env.BASEROW_TOKEN }}")},
               body=x('{{ $json.body_json }}'), onError="continueRegularOutput", retry=2, always=True)
    wf.link(after, body)
    wf.chain(body, ex)
    wf.link(ex, upd, 0)
    wf.link(ex, new, 1)
    return [upd, new]


def tg(wf: WF, label: str, text: str, *, notes: str = "", always: bool = True) -> str:
    """Telegram via plain HTTPS — no n8n credential to create."""
    return http(wf, label, x("{{ 'https://api.telegram.org/bot' + $env.TELEGRAM_BOT_TOKEN + '/sendMessage' }}"),
                method="POST",
                body={"chat_id": x("{{ $env.TELEGRAM_CHAT_ID }}"), "text": x(text),
                      "parse_mode": "HTML", "disable_web_page_preview": "false"},
                notes=notes or "Telegram = free alerts. Skip node if TELEGRAM_BOT_TOKEN is empty.",
                onError="continueRegularOutput", always=always)


def waha(wf: WF, label: str, body: dict, path: str = "/api/sendText", *, notes: str = "",
         always: bool = True, disabled: bool = False, onError: str | None = "continueRegularOutput") -> str:
    return http(wf, label, x("{{ $env.WAHA_URL + '" + path + "' }}"), method="POST",
                headers={"Content-Type": "application/json",
                         "X-Api-Key": x("{{ $env.WAHA_API_KEY }}")},
                body=body, notes=notes, onError=onError, always=always, disabled=disabled)


# ======================================================= 01 LEADS AND DEMOS ===
def build_leads() -> WF:
    wf = WF("01-leads-and-demos", "roz subah: OSM leads -> AI demo site -> Pages deploy -> CRM")
    a = sched(wf, "Daily 06:00", "LEADS_CRON", notes="cron from .env LEADS_CRON (default: 0 6 * * *)")
    quota = code(wf, "Budget", """
// today's capacity; a manual re-run in the same day continues instead of repeating
const max = parseInt($env.MAX_LEADS_PER_DAY || '20', 10);
return [{ json: { max, date: new Date().toISOString().slice(0, 10) } }];
""")
    wf.chain(a, quota)
    lst = crm_list(wf, "Read CRM", after="Budget",
                   notes="supplies known lead_key values so we never rebuild a sent demo")
    scan = gw(wf, "Radar: scan OSM", "/v1/leads/scan",
              {"campaign": "all", "limit": x("{{ $('Budget').item.json.max * 3 }}"),
               "exclude_keys": x("{{ $('Read CRM').item.json.keys || '' }}")},
              notes="OpenStreetMap Overpass (free, no key). Returns ONLY businesses with no real website.",
              retry=2, onError="continueErrorOutput")
    wf.link(lst, scan)
    prep = code(wf, "Prepare leads", """
// hard filters before we spend any AI quota: reachable, named, not already in the CRM, max/day
const raw = $input.first().json || {};
const src = Array.isArray(raw.leads) ? raw.leads : [];
const max = parseInt($env.MAX_LEADS_PER_DAY || '20', 10);
const known = (($('Read CRM').item.json || {}).rows) || [];
const rowId = {};
for (const r of known) if (r.lead_key) rowId[r.lead_key] = r.row_id || '';
const SKIP = /chain|bank|mosque|temple|church|fuel|atm|pharmacy chain|municipal|government|post office/i;
const seen = new Set();
const out = [];
for (const l of src) {
  const name = String(l.osm_name || l.name || '').trim();
  if (!name || !l.key || seen.has(l.key)) continue;
  if (seen.has(name.toLowerCase())) continue;
  if (!(l.whatsapp || l.phone)) continue;
  if (SKIP.test(name) || SKIP.test(l.category_label || '')) continue;
  if (!l.address && !l.city) continue;
  seen.add(l.key); seen.add(name.toLowerCase());
  out.push({ json: {
    op: 'upsert', lead_key: l.key, business_name: name.slice(0, 120),
    category: l.category || 'services', category_label: l.category_label || 'Local business',
    city: l.city || '', area: l.area || '', country: (l.country || '').toUpperCase(),
    address: l.address || '', phone: l.phone || '', whatsapp: l.whatsapp || '',
    website: l.website || '', rating: l.rating || '', reviews: l.reviews_count || '',
    hours: l.opening_hours || '', notes: (l.notes || '').slice(0, 900), emoji: l.emoji || '\\ud83c\\udfea',
    hotness: Number(l.hotness || 0), slug: l.slug || '', language: l.language || 'auto',
    price_label: l.price_label || '', demo_url: '', status: 'scanned', source: 'overpass',
    reply_count: 0, outbound_count: 0, ai_reply_count: 0, followup_count: 0, paid: 'no',
    _stats: JSON.stringify(raw.stats || {}), row_id: rowId[l.key] || '',
  } });
  if (out.length >= max) break;
}
if (!out.length) {
  return [{ json: { empty: true, _stats: JSON.stringify(raw.stats || {}),
                    _err: ((raw.stats || {}).errors || []).slice(0, 2).join(' | ') || 'no fresh OSM leads' } }];
}
return out;
""", always=True)
    wf.chain(scan, prep)
    any_lead = ifs(wf, "Naye leads?", "{{ $json.empty !== true }}")
    wf.chain(prep, any_lead)
    loop = sib(wf, "Per lead")
    wf.link(any_lead, loop, 0)
    gen = gw(wf, "AI: generate demo site", "/v1/demo/generate", {
        "business_name": x("{{ $json.business_name }}"), "category": x("{{ $json.category }}"),
        "category_label": x("{{ $json.category_label }}"), "address": x("{{ $json.address }}"),
        "city": x("{{ $json.city }}"), "area": x("{{ $json.area }}"), "country": x("{{ $json.country }}"),
        "phone": x("{{ $json.phone }}"), "whatsapp": x("{{ $json.whatsapp }}"),
        "rating": x("{{ $json.rating }}"), "reviews_count": x("{{ $json.reviews }}"),
        "opening_hours": x("{{ $json.hours }}"), "notes": x("{{ $json.notes }}"),
        "slug": x("{{ $json.slug }}"), "language": x("{{ $json.language }}")},
        notes="gateway: design seed -> prompt -> 1 self-repair pass -> HTML quality gate",
        retry=1, onError="continueErrorOutput")
    wf.link(loop, gen)
    ok = ifs(wf, "Site passed gate?", "{{ $json.ok === true && Boolean($json.html) }}",
             notes="false = the model output failed our checks twice (see CRM note)")
    wf.chain(gen, ok)
    dep = gw(wf, "Deploy to Pages", "/v1/site/deploy", {
        "slug": x("{{ $('Per lead').item.json.slug }}"), "html": x("{{ $json.html }}"),
        "title": x("{{ $json.title }}"), "meta_description": x("{{ $json.meta_description }}"),
        "business_name": x("{{ $('Per lead').item.json.business_name }}"),
        "area": x("{{ $('Per lead').item.json.area }}")},
        notes="GitHub/CF Pages, then polls the URL until it really returns 200", retry=1,
        onError="continueErrorOutput")
    wf.link(ok, dep, 0)
    save_ok = code(wf, "Result: demo_ready", """
const lead = $('Per lead').item.json;
const site = $('AI: generate demo site').first().json;
const d = $input.first().json || {};
return [{ json: {
  op: 'upsert', row_id: lead.row_id || '', lead_key: lead.lead_key, business_name: lead.business_name,
  category: lead.category, category_label: lead.category_label, city: lead.city, area: lead.area,
  country: lead.country, address: lead.address, phone: lead.phone, whatsapp: lead.whatsapp,
  website: lead.website, rating: lead.rating, reviews: lead.reviews, hours: lead.hours,
  hotness: lead.hotness, emoji: lead.emoji, notes: lead.notes, language: lead.language,
  demo_slug: d.slug || lead.slug, demo_url: d.url || '', design: site.design || {},
  site_bytes: site.bytes || 0, ai_provider: site.provider || '', status: d.live ? 'demo_ready' : 'deploy_pending',
  deploy_target: d.target || '', deploy_note: d.note || '', source: 'overpass',
  next_action_at: new Date().toISOString().slice(0, 10),
} }];
""", always=True, notes="row_id kept from the CRM read so this updates instead of duplicating")
    wf.chain(dep, save_ok)
    ok_exits = crm_upsert(wf, "CRM: save demo", after="Result: demo_ready")
    fail = code(wf, "Result: failed", """
const lead = $('Per lead').item.json;
const j = $input.first().json || {};
const msg = String(j.error || j.message || (j.statusCode ? 'HTTP ' + j.statusCode : '') ||
                   JSON.stringify(j).slice(0, 200)).slice(0, 500);
return [{ json: { op: 'upsert', row_id: '', lead_key: lead.lead_key, business_name: lead.business_name,
  category: lead.category, category_label: lead.category_label, city: lead.city, area: lead.area,
  country: lead.country, phone: lead.phone, whatsapp: lead.whatsapp, hotness: lead.hotness,
  emoji: lead.emoji, notes: ('demo failed: ' + msg).slice(0, 1400), status: 'demo_failed',
  language: lead.language, address: lead.address, website: lead.website,
  row_id: lead.row_id || '',
  next_action_at: new Date(Date.now() + 3 * 864e5).toISOString().slice(0, 10) } }];
""", always=True, notes="failures are recorded too — that is how you notice a dead API key")
    wf.link(ok, fail, 1)
    wf.link(gen, fail, 1)
    bad_exits = crm_upsert(wf, "CRM: save failure", after="Result: failed")
    breathe = wait(wf, "AI quota breather", "{{ 25 + Math.floor(Math.random() * 30) }}",
                   notes="spreads calls so a free tier RPM/RPD cap is not hit mid-loop")
    for e in list(ok_exits) + list(bad_exits):
        wf.link(e, breathe)
    wf.link(breathe, loop)
    count = code(wf, "Summary", """
const prep = $('Prepare leads').allItems ? $('Prepare leads').allItems().map(i => i.json) : [];
const leads = prep.filter(i => !i.empty);
let ready = 0, failed = 0;
try { ready = $('Result: demo_ready').allItems().length; } catch (e) {}
try { failed = $('Result: failed').allItems().filter(i => i.json.lead_key).length; } catch (e) {}
const stats = JSON.parse((leads[0] || prep[0] || {})._stats || '{}');
return [{ json: {
  scanned: stats.osm_raw || 0, fresh: leads.length, ready, failed,
  errors: (stats.errors || []).slice(0, 3).join(' | '),
  lines: leads.slice(0, 10).map(l => '\\u2022 ' + l.business_name + ' (' + (l.city || l.area) + ') hotness ' + l.hotness).join('\\n'),
} }];
""", always=True)
    wf.link(loop, count, 1)
    wf.link(any_lead, count, 0)
    alert = tg(wf, "Telegram: demos ready", """{{
  "<b>\\ud83c\\udf88 Demo sites ban gaye: " + $json.ready + "</b>\\n" +
  "OSM scan: " + $json.scanned + " POI \\u2192 " + $json.fresh + " naye leads\\n" +
  ($json.lines ? $json.lines + "\\n" : "") +
  ($json.failed ? "\\u26a0\\ufe0f " + $json.failed + " fail (CRM me 'demo_failed' dekh lein)\\n" : "") +
  ($json.errors ? "\\u26a0\\ufe0f " + $json.errors + "\\n" : "") +
  "Workflow 02 aaj inhein bhej dega."
}}""", notes="yahi aapki subah ki report hai")
    wf.chain(count, alert)
    none = tg(wf, "Telegram: no leads", """{{
  "\\ud83d\\udd0d Aaj koi naya lead nahi.\\n" + ($json._err || '') +
  "\\nCheck: config/campaigns/default.json me areas enabled hain? OVERPASS_URL reachable?"
}}""")
    wf.link(any_lead, none, 1)
    scan_fail = tg(wf, "Telegram: scan failed", """{{
  "\\ud83d\\udea9 Lead scan FAIL: " + String($json.error || $json.message || 'gateway unreachable').slice(0, 300) +
  "\\n docker compose logs ai-gateway --tail 40"
}}""", notes="gateway down / no AI key -> you hear about it immediately, not tomorrow")
    wf.link(scan, scan_fail, 1)
    return wf


# =============================================================== 02 OUTREACH ===
def build_outreach() -> WF:
    wf = WF("02-outreach", "queue -> AI message -> WhatsApp/email, rate limited + random delay")
    a = sched(wf, "Send windows", "OUTREACH_CRON", notes="two windows/day, Mon-Sat (OUTREACH_CRON in .env)")
    health = http(wf, "WAHA status", x("{{ $env.WAHA_URL + '/api/sessions/' + $env.WAHA_SESSION }}"),
                  headers={"X-Api-Key": x("{{ $env.WAHA_API_KEY }}")}, onError="continueRegularOutput",
                  always=True, notes="if the session is not active we must not half-send")
    wf.chain(a, health)
    guard = ifs(wf, "WhatsApp connected?", "{{ ($json.status && ($json.status.state || $json.status)) == "
                                           "'active' || (!$json.status && $json.statusCode !== 404) }}",
                notes="true = sendable. False = WAHA down -> we alert instead of queueing.")
    wf.chain(health, guard)
    lst = crm_list(wf, "Read CRM", after="WhatsApp connected?", after_out=0)
    q = code(wf, "Build queue", """
// quiet hours + daily cap + no-demo-no-send + best-first ordering
const rows = ($input.first().json || {}).rows || [];
const MAX = parseInt($env.MAX_OUTREACH_PER_DAY || '25', 10);
const now = new Date();
const mins = now.getHours() * 60 + now.getMinutes();
const qs = parseInt($env.QUIET_START_HOUR || '9', 10) * 60;
const qe = parseInt($env.QUIET_END_HOUR || '21', 10) * 60;
if (mins < qs || mins >= qe) {
  return [{ json: { empty: true, reason: 'quiet hours (' + qs / 60 + ':00-' + qe / 60 + ':00 local)' } }];
}
const ACTIVE = ['contacted', 'replied', 'interested', 'negotiating', 'paid', 'followup_sent',
                'revision_requested', 'stalled'];
const sentToday = rows.filter(r => r._contacted_today && ACTIVE.includes(String(r.status || ''))).length;
let room = MAX - sentToday;
if (room <= 0) return [{ json: { empty: true, reason: 'daily cap ' + MAX + ' already sent today' } }];
const today = new Date().toISOString().slice(0, 10);
const score = r => (Number(r.hotness) || 0) + (r.whatsapp ? 25 : 0) + (Number(r.rating) >= 4.3 ? 10 : 0)
                   - (Number(r.outbound_count) || 0) * 45;
const cands = rows
  .filter(r => r.lead_key)
  .filter(r => ['demo_ready', 'queued', 'stalled'].includes(String(r.status || '')))
  .filter(r => r.demo_url && /^https:\\/\\//.test(String(r.demo_url)))
  .filter(r => !r._contacted_today)
  .filter(r => !r.next_action_at || String(r.next_action_at) <= today)
  .filter(r => (Number(r.outbound_count) || 0) < 3)
  .sort((a, b) => score(b) - score(a));
const out = [];
for (const r of cands) {
  if (room <= 0) break;
  if (!r.whatsapp && !r.phone && !r.email) continue;
  room--;
  out.push({ json: r });
}
if (!out.length) {
  return [{ json: { empty: true, reason: 'koi demo_ready lead pending nahi (01 chalayein ya CRM status dekhein)' } }];
}
return out;
""", always=True)
    wf.chain(lst, q)
    has = ifs(wf, "Koi bhejne ko?", "{{ $json.empty !== true }}")
    wf.chain(q, has)
    loop = sib(wf, "Per lead")
    wf.link(has, loop, 0)
    msg = gw(wf, "AI: write message", "/v1/outreach", {
        "business_name": x("{{ $json.business_name }}"), "category_label": x("{{ $json.category_label }}"),
        "city": x("{{ $json.city }}"), "area": x("{{ $json.area }}"), "country": x("{{ $json.country }}"),
        "demo_url": x("{{ $json.demo_url }}"), "whatsapp": x("{{ $json.whatsapp || $json.phone }}"),
        "rating": x("{{ $json.rating }}"), "reviews_count": x("{{ $json.reviews }}"),
        "language": x("{{ $json.language || 'auto' }}"), "notes": x("{{ $json.notes }}"),
        "gap_note": x("{{ $json.website ? 'unke paas sirf social/old link hai: ' + $json.website : '' }}")},
        notes="Roman Urdu for PK/BD/IN, English elsewhere; 45-word cap enforced by the prompt",
        retry=2, onError="continueErrorOutput")
    wf.link(loop, msg)
    safe = code(wf, "Message safety gate", """
// Nothing leaves until it passes. A banned phrase or a leaked price can cost you the number.
const lead = $('Per lead').item.json;
const raw = $input.first().json || {};
let m = String(raw.message || '').trim();
const BANNED = /(dear sir|dear madam|valued customer|i hope this.{0,20}finds you well|limited (period )?offer|100% free|guaranteed|digital presence|boost your (sales|business)|SEO audit|\\ud83d\\ude80|\\ud83d\\udcc8|\\ud83d\\udcb0|\\u2764\\ufe0f\\u2764\\ufe0f)/i;
const problems = [];
const link = String(lead.demo_url || '').trim();
if (!m) problems.push('empty');
if (m.length > 560) problems.push('too long ' + m.length);
if (BANNED.test(m)) problems.push('banned phrase');
if (!link) problems.push('no demo url');
if (link && m.indexOf(link) === -1) problems.push('link not in message');
if (/(PKR|Rs\\.?)\\s?[0-9]{1,3}[,.]?[0-9]{3}|USD\\s?[0-9]{2,3}|\\bprice\\b/i.test(m)) problems.push('price in first touch');
const emoji = (m.match(/[\\ud83c\\udf00-\\ud83e\\uddff]/gu) || []).length;
if (emoji > 1) problems.push('emoji spam ' + emoji);
const fallback = () => {
  const nm = String(lead.business_name || 'aap').split(' ').slice(0, 2).join(' ');
  const urdu = String(lead.language || '').indexOf('urdu') >= 0 || ['PK', 'BD', 'IN'].includes(String(lead.country||'').toUpperCase());
  return urdu
    ? nm + ', Google par aapki ' + (lead.category_label || 'dukaan') + ' hai magar website nahi.\\n' +
      'Maine aapke liye ek page bana diya \\u2014 details, timings aur WhatsApp button ke saath.\\n' + link +
      '\\nDekh lein. Pasand aaye to baat karte hain, abhi koi charge nahi.'
    : nm + ', you show up on Google but have no website.\\nI made a page for you \\u2014 details, hours, ' +
      'and a WhatsApp button.\\n' + link + '\\nFree to look at. If you like it, we can talk.';
};
if (problems.length) m = fallback();
if (link && m.indexOf(link) === -1) m = m.replace(/https?:\\/\\/\\S+/, '') .trim() + '\\n' + link;
const phone = String(lead.whatsapp || lead.phone || '').replace(/\\D/g, '');
return [{ json: {
  body: m.slice(0, 560), words: m.trim().split(/\\s+/).length, chatId: phone ? phone + '@c.us' : '',
  email: lead.email || '', channel: phone ? 'whatsapp' : 'email', fixed: problems.length > 0,
  problems: problems.join(', '), lead_key: lead.lead_key, row_id: lead.row_id } }];
""", always=True)
    wf.chain(msg, safe)
    chan = ifs(wf, "WhatsApp number?", "{{ Boolean($json.chatId) }}")
    wf.chain(safe, chan)
    typing = waha(wf, "WAHA: typing", {"session": x("{{ $env.WAHA_SESSION }}"),
                                       "chatId": x("{{ $('Message safety gate').item.json.chatId }}")},
                  "/api/startTyping", notes="typing indicator first = reads as human")
    wf.link(chan, typing, 0)
    pause = wait(wf, "Looks human", "{{ 3 + Math.floor(Math.random() * 5) }}")
    wf.chain(typing, pause)
    send = waha(wf, "WAHA: sendText", {"session": x("{{ $env.WAHA_SESSION }}"),
                                      "chatId": x("{{ $('Message safety gate').item.json.chatId }}"),
                                      "text": x("{{ $('Message safety gate').item.json.body }}")},
                 "/api/sendText", notes="429/blocked -> error branch records send_failed",
                 onError="continueErrorOutput")
    wf.chain(pause, send)
    email_node = wf.add("Gmail: send (optional)", "n8n-nodes-base.emailSend", 2.1, {
        "fromEmail": x("{{ $env.GMAIL_SMTP_FROM }}"),
        "toEmail": x("{{ $('Message safety gate').item.json.email }}"),
        "subject": x("{{ $('AI: write message').item.json.subject }}"),
        "text": x("{{ $('Message safety gate').item.json.body }}")},
        notes="DISABLED by default. To use email: create an SMTP credential (README step 7), "
              "then enable this node and set the app password.",
        disabled=True, onError="continueErrorOutput",
        credentials={"smtp": {"id": "REPLACE_ME", "name": "Gmail SMTP"}})
    wf.link(chan, email_node, 1)
    sent = code(wf, "Log send", """
const lead = $('Per lead').item.json;
const gate = $('Message safety gate').item.json;
const j = $input.first().json || {};
const err = j.error || j.message || (j.statusCode ? 'HTTP ' + j.statusCode : '');
const ok = !err && (j.id || j.messageId || gate.channel === 'email');
return [{ json: {
  op: 'upsert', row_id: lead.row_id || '', lead_key: lead.lead_key, business_name: lead.business_name,
  status: ok ? 'contacted' : 'send_failed', sent_at: new Date().toISOString().slice(0, 10),
  outbound_count: (Number(lead.outbound_count) || 0) + (ok ? 1 : 0),
  last_outbound: gate.body, outreach_channel: gate.channel, demo_url: lead.demo_url,
  category: lead.category, category_label: lead.category_label, city: lead.city,
  country: lead.country, whatsapp: lead.whatsapp, phone: lead.phone, hotness: lead.hotness,
  language: lead.language, reply_count: Number(lead.reply_count) || 0,
  ai_notes: (gate.channel + (gate.fixed ? ' fallback(' + gate.problems + ')' : '') +
             (ok ? ' ok' : ' ERR ' + String(err).slice(0, 120))).slice(0, 380),
  next_action_at: ok ? new Date(Date.now() + 2 * 864e5).toISOString().slice(0, 10)
                     : new Date().toISOString().slice(0, 10) } }];
""", always=True)
    wf.chain(send, sent)
    wf.link(email_node, sent)
    mark_exits = crm_upsert(wf, "CRM: mark contacted", after="Log send")
    gap = wait(wf, "Random delay (anti-ban)", x(
        "{{ Math.floor(Math.random() * Math.max(60, Number($env.OUTREACH_MAX_DELAY_SECONDS || 280))) "
        "+ Number($env.OUTREACH_MIN_DELAY_SECONDS || 90) }}"),
        notes="90-280s by default. Below 60s is how numbers get banned \\u2014 don't.")
    for e in mark_exits:
        wf.link(e, gap)
    wf.link(gap, loop)
    summary = code(wf, "Run summary", """
const rows = $('Build queue').allItems ? $('Build queue').allItems().map(i => i.json) : [];
const queued = rows.filter(r => !r.empty).length;
let sent = 0;
try { sent = $('Log send').allItems().filter(i => i.json.status === 'contacted').length; } catch (e) {}
let failed = 0;
try { failed = $('Log send').allItems().filter(i => i.json.status === 'send_failed').length; } catch (e) {}
return [{ json: { queued, sent, failed, reason: (rows.find(r => r.empty) || {}).reason || '' } }];
""", always=True)
    wf.link(loop, summary, 1)
    wf.link(has, summary, 0)
    tgsum = tg(wf, "Telegram: sent", """{{
  $json.queued
    ? "\\ud83d\\udce4 Outreach: " + $json.sent + " bheje, " + $json.failed + " fail (cap " + ($env.MAX_OUTREACH_PER_DAY || 25) + "/day)\\n" +
      "Reply aane par 03 khud classify karega."
    : "\\u2139\\ufe0f Outreach skip: " + $json.reason
}}""")
    wf.chain(summary, tgsum)
    dead = tg(wf, "Telegram: WAHA down", """{{
  "\\ud83d\\udea8 WhatsApp session ACTIVE nahi \\u2192 outreach rok diya (aadhe-adhure message na jayein).\\n" +
  "http://localhost:3000/dashboard \\u2192 session start \\u2192 QR dobara scan.\\n" +
  "docker compose logs waha --tail 30"
}}""", notes="fixed before it silently does nothing for a week")
    wf.link(guard, dead, 1)
    return wf


# ================================================================= 03 REPLIES ===
def build_replies() -> WF:
    wf = WF("03-replies", "WAHA webhook -> AI classify -> auto-reply / Telegram / CRM status")
    hook = wf.add("WAHA webhook", HOOK, 2, {"httpMethod": "POST", "path": "waha",
                                            "responseMode": "onReceived", "options": {"rawBody": False}},
                  notes="set WAHA env WHATSAPP_HOOK_URL = <public url>/webhook/waha")
    ack = wf.add("ACK 200", RESPOND, 1.1, {"respondWith": "json",
                                           "responseBody": "={{ JSON.stringify({ok:true}) }}",
                                           "options": {"responseCode": 200}},
                 notes="ack first, then think \\u2014 otherwise WAHA retries and you reply twice")
    wf.chain(hook, ack)
    parse = code(wf, "Parse event", """
// Only real, human, 1:1 inbound text. Everything else exits here (silently, on purpose).
const e = $input.first().json || {};
const body = e.body || e;
const d = body._data || body;
const event = String(body.event || d.eventType || '').toLowerCase();
const from = String(d.from || d.chatId || '');
const out = (o) => [{ json: { skip: true, why: o } }];
if (event && event !== 'message' && event !== 'message.any') return out('event=' + event);
if (!from) return out('no from');
if (d.fromMe || d.isFromMe) return out('my own message');
if (/@(g\\.us|newsletter|broadcast|lid)$/.test(from) || from === 'status@broadcast') return out('group/channel');
const t = d.text || d.body
  || (d.message && (d.message.conversation
      || (d.message.extendedTextMessage && d.message.extendedTextMessage.text))) || '';
let text = String(t).trim();
const media = Boolean(d.hasMedia || d.mimetype || d.type === 'image' || d.type === 'audio');
if (!text && media) text = '[media: ' + (d.mimetype || 'file') + ']';
if (!text) return out('empty body');
const chatId = from.includes('@') ? from : from + '@c.us';
return [{ json: {
  skip: false, chatId, phone: chatId.split('@')[0], text: text.slice(0, 1800),
  sender: String(d.notifyName || d._data && d._data.notifyName || d.contact || '').slice(0, 60),
  msgId: d.id || '', ts: new Date(Number(d.timestamp || 0) * 1000 || Date.now()).toISOString(),
  media, seen: Boolean(d.id && (d.ack >= 3 || d.ack === 'READ')) } }];
""", always=True)
    wf.chain(ack, parse)
    real = ifs(wf, "Inbound human?", "{{ $json.skip !== true }}")
    wf.chain(parse, real)
    lst = crm_list(wf, "Read CRM", after="Inbound human?",
                   notes="stateless match by phone tail - no DB of threads needed")
    wf.link(real, lst, 0)
    match = code(wf, "Find lead", """
// OSM phones vary (0300..., +92 300..., 92300...), so match on the last 10 digits.
const ev = $('Parse event').item.json;
const rows = ($input.first().json || {}).rows || [];
const tail = s => String(s || '').replace(/\\D/g, '').slice(-10);
const mine = tail(ev.phone);
const row = rows.find(r => mine && (tail(r.whatsapp) === mine || tail(r.phone) === mine));
if (!row) {
  return [{ json: { matched: false, chatId: ev.chatId, phone: ev.phone, text: ev.text, ts: ev.ts,
                    sender: ev.sender, msgId: ev.msgId, media: ev.media } }];
}
const hrs = row.sent_at ? (Date.now() - new Date(String(row.sent_at).slice(0, 10)).getTime()) / 36e5 : 9999;
return [{ json: { matched: true, chatId: ev.chatId, phone: ev.phone, text: ev.text, ts: ev.ts, media: ev.media,
  msgId: ev.msgId, sender: ev.sender || row.business_name, lead_key: row.lead_key, row_id: row.row_id || '',
  business_name: row.business_name, category_label: row.category_label, city: row.city,
  country: row.country, demo_url: row.demo_url, status: row.status, language: row.language || 'auto',
  ai_reply_count: Number(row.ai_reply_count) || 0, reply_count: Number(row.reply_count) || 0,
  outbound_count: Number(row.outbound_count) || 0, last_outbound: row.last_outbound || '',
  notes: row.notes || '', whatsapp: row.whatsapp, hotness: row.hotness,
  thread_age_hours: Math.round(hrs) } }];
""", always=True)
    wf.chain(lst, match)
    known = ifs(wf, "CRM me lead?", "{{ $json.matched === true }}",
                notes="unknown number replied -> alert you, NEVER auto-reply to a stranger")
    wf.chain(match, known)
    ai = gw(wf, "AI: classify reply", "/v1/reply/classify", {
        "message_text": x("{{ $('Find lead').item.json.text }}"),
        "business_name": x("{{ $('Find lead').item.json.business_name }}"),
        "sender_name": x("{{ $('Find lead').item.json.sender }}"),
        "category_label": x("{{ $('Find lead').item.json.category_label }}"),
        "city": x("{{ $('Find lead').item.json.city }}"), "country": x("{{ $('Find lead').item.json.country }}"),
        "demo_url": x("{{ $('Find lead').item.json.demo_url }}"),
        "thread_age_hours": x("{{ String($('Find lead').item.json.thread_age_hours || 0) }}"),
        "ai_replies_in_thread": x("{{ String($('Find lead').item.json.ai_reply_count || 0) }}"),
        "last_outbound": x("{{ $('Find lead').item.json.last_outbound }}"),
        "language": x("{{ $('Find lead').item.json.language }}")},
        notes="1 call = intent + should_reply + draft + CRM status (quota friendly)", retry=1,
        onError="continueErrorOutput")
    wf.link(known, ai, 0)
    decide = code(wf, "Decide", """
// final say on whether the bot may speak: model output + your caps + your guardrails
const lead = $('Find lead').item.json;
const c = $input.first().json || {};
const intent = String(c.intent || 'unclear').toLowerCase();
const enabled = String($env.AUTO_REPLY_ENABLED || 'true').toLowerCase().charAt(0) === 't';
const cap = parseInt($env.AUTO_REPLY_MAX_PER_THREAD || '2', 10);
const RISKY = ['not_interested', 'handoff_needed', 'auto_reply', 'spam', 'objection_price', 'question_other'];
const conf = Number(c.confidence || 0);
let allow = Boolean(c.should_reply) && enabled && (Number(lead.ai_reply_count) || 0) < cap
            && conf >= 0.55 && RISKY.indexOf(intent) === -1 && String(c.reply || '').trim().length > 2;
const notify = ['interested', 'price_question', 'revision_request', 'objection_trust', 'handoff_needed',
                'not_interested', 'objection_price'].indexOf(intent) >= 0 || conf < 0.55 ||
               String($env.ALERT_EVERY_REPLY || 'false') === 'true';
const MAP = { interested: 'interested', price_question: 'negotiating', objection_price: 'negotiating',
  objection_trust: 'negotiating', revision_request: 'revision_requested', not_interested: 'not_interested',
  handoff_needed: 'handoff', auto_reply: 'auto_reply', spam: 'spam', question_other: 'replied',
  unclear: 'replied' };
let st = (c.lead_status && c.lead_status !== 'keep_current') ? c.lead_status : (MAP[intent] || 'replied');
const paid = intent === 'interested' && /screenshot|txn|transaction|paid|payment done|bhej diya|dekh lein? payment/i
  .test(String(c.reply || '') + ' ' + lead.text);
return [{ json: { ...c, ...lead, intent, confidence: conf, allow_reply: allow, notify,
  new_status: paid ? 'payment_claimed' : st,
  payment: Boolean(c.payment_info_sent), guard: allow ? '' : (enabled ? 'held' : 'auto-reply off'),
  notes: String(lead.notes || '').slice(-1200),
  next_followup: ['not_interested', 'spam', 'paid', 'payment_claimed'].indexOf(st) >= 0 ? ''
    : new Date(Date.now() + 864e5).toISOString().slice(0, 10),
  updated: new Date().toISOString() } }];
""", always=True)
    wf.chain(ai, decide)
    upd_body = code(wf, "CRM update", """
const d = $input.first().json;
return [{ json: {
  op: 'upsert', row_id: d.row_id || '', lead_key: d.lead_key, business_name: d.business_name,
  status: d.new_status, intent: d.intent, last_inbound: d.ts,
  reply_count: (Number(d.reply_count) || 0) + 1,
  ai_reply_count: (Number(d.ai_reply_count) || 0) + (d.allow_reply ? 1 : 0),
  demo_url: d.demo_url, language: d.language, city: d.city, country: d.country,
  category_label: d.category_label, phone: d.phone, whatsapp: d.whatsapp, hotness: d.hotness,
  payment_info_sent: d.payment ? 'yes' : 'no',
  next_action_at: d.next_followup,
  notes: (String(d.notes) + ' | ' + String(d.ts).slice(0, 10) + ' [' + d.intent + '] ' +
          String(d.text).slice(0, 160) + (d.allow_reply ? ' | ai-replied' : ' | held'))
         .slice(-1500) } }];
""", always=True)
    wf.link(decide, "CRM update")
    save_exits = crm_upsert(wf, "CRM: save reply", after="CRM update")
    seen = waha(wf, "WAHA: mark read", {"session": x("{{ $env.WAHA_SESSION }}"),
                                        "chatId": x("{{ $('Find lead').item.json.chatId }}")},
                 "/api/sendSeen", notes="blue ticks while a human is about to answer = good service")
    wf.link(decide, seen)
    may = ifs(wf, "Bot bole?", "{{ $json.allow_reply === true }}")
    wf.chain(decide, may)
    rep = waha(wf, "WAHA: reply", {"session": x("{{ $env.WAHA_SESSION }}"),
                                   "chatId": x("{{ $('Decide').item.json.chatId }}"),
                                   "text": x("{{ $('Decide').item.json.reply }}"),
                                   "reply_to": x("{{ $('Decide').item.json.msgId }}")},
                "/api/sendText", notes="threads the reply (reply_to) so context is obvious",
                onError="continueErrorOutput")
    wf.link(may, rep, 0)
    count_up = code(wf, "Count reply sent", """
const j = $('Decide').item.json;
return [{ json: { op: 'upsert', row_id: j.row_id || '', lead_key: j.lead_key,
  last_ai_reply: String(j.reply || '').slice(0, 500), business_name: j.business_name } }];
""", always=True)
    wf.link(rep, count_up)
    crm_upsert(wf, "CRM: ai_reply", after="Count reply sent")
    alert = tg(wf, "Telegram: alert", """{{
  "<b>" + ($json.intent === 'interested' ? '\\ud83d\\udd25 INTERESTED \\u2014 paisa aaj aa sakta hai'
        : $json.intent === 'not_interested' ? '\\u274c Not interested (filed)'
        : $json.intent === 'handoff_needed' ? '\\ud83e\\uddd1 Aapko khud jawab dena hai'
        : '\\ud83d\\udcac ' + String($json.intent).toUpperCase()) + "</b>\\n" +
  $json.business_name + " \\u2014 " + ($json.category_label || '') + ", " + ($json.city || '') + "\\n" +
  "\\ud83d\\udcf1 https://wa.me/" + ($json.phone || String($json.chatId || '').split('@')[0]) + "\\n" +
  "\\ud83d\\udd17 " + ($json.demo_url || 'no demo') + "\\n" +
  "\\ud83d\\udcac " + String($json.text || '').slice(0, 400) + "\\n" +
  "\\ud83e\\udd16 " + String($json.summary || 'classified: ' + $json.intent) + "\\n" +
  ($json.allow_reply ? "\\u2705 AI ne reply kar diya: " + String($json.reply || '').slice(0, 220) + "\\n" +
     ($json.ai_reply_count + 1) + "/" + ($env.AUTO_REPLY_MAX_PER_THREAD || 2) + " AI replies in thread"
   : "\\u23f8\\ufe0f reply HOLD (" + ($json.guard || 'risk intent') + ") \\u2192 aap bhejein") +
  ($json.payment ? "\\n\\ud83d\\udcb8 payment details bhej diye: " + ($env.PAY_METHODS || '') : '') +
  ($json.intent === 'interested' ? "\\n\\u27a1\\ufe0f Ab: " + ($env.PAY_INSTRUCTIONS || 'payment confirm karein') : '')
}}""", notes="din ka sirf yahi message padhna hai \\u2014 baaki system sambhaal leta hai")
    wf.link(may, alert, 1)
    ai_fail = code(wf, "Classify failed", """
// AI down = do not auto-reply, but ALWAYS tell the human (a missed 'haan' is lost revenue)
const lead = $('Find lead').item.json;
const j = $input.first().json || {};
return [{ json: { op: 'upsert', row_id: lead.row_id || '', lead_key: lead.lead_key,
  business_name: lead.business_name, status: lead.status || 'replied', intent: 'model_error',
  last_inbound: lead.ts, reply_count: (Number(lead.reply_count) || 0) + 1, demo_url: lead.demo_url,
  whatsapp: lead.whatsapp, phone: lead.phone, city: lead.city, hotness: lead.hotness,
  next_action_at: new Date().toISOString().slice(0, 10),
  notes: (String(lead.notes || '') + ' | ai-down: ' + String(j.error || j.message || 'classify failed')
          .slice(0, 160)).slice(-1500),
  _alert: String(lead.text || '').slice(0, 300) } }];
""", always=True)
    wf.link(ai, ai_fail, 1)
    fail_exits = crm_upsert(wf, "CRM: ai_down", after="Classify failed")
    tgfail = tg(wf, "Telegram: AI down", """{{
  "\\u26a0\\ufe0f Reply aaya magar AI classify nahi kar paya \\u2014 aap khud jawab dein.\\n" +
  $json.business_name + ": " + ($json._alert || '') + "\\nwa.me/" + ($json.phone || '') +
  "\\ndocker compose logs ai-gateway --tail 30"
}}""")
    for e in fail_exits:
        wf.link(e, tgfail)
    stranger = code(wf, "Unknown number", """
const ev = $('Find lead').item.json;
return [{ json: { op: 'upsert', lead_key: 'wa/' + ev.phone, business_name: ev.sender || 'Unknown number',
  phone: ev.phone, whatsapp: ev.phone, status: 'inbound_unmatched', source: 'manual', intent: 'handoff_needed',
  last_inbound: ev.ts, reply_count: 1, outbound_count: 0, ai_reply_count: 0, paid: 'no',
  hotness: 0, next_action_at: new Date().toISOString().slice(0, 10),
  notes: ('bina CRM entry message: ' + String(ev.text).slice(0, 400)).slice(0, 1500),
  _chat: ev.chatId, _msg: ev.msgId } }];
""", always=True)
    wf.link(known, stranger, 1)
    sup = crm_upsert(wf, "CRM: log stranger", after="Unknown number")
    tg2 = tg(wf, "Telegram: new number", """{{
  "\\ud83d\\udd14 Koi anjaan number ne message kiya (CRM me nahi):\\n" + String($json.notes || '').slice(0, 300) +
  "\\n\\ud83d\\udcf1 https://wa.me/" + ($json.phone || '') + "\\n" +
  "Dhyan rahe: isko bina soche outreach ka jawab mat dein \\u2014 01 se lead add karein."
}}""")
    wf.link("CRM: log stranger · body", tg2)
    skip = code(wf, "Ignored", """
const j = $input.first().json || {};
return [{ json: { why: j.why || 'not actionable', at: new Date().toISOString() } }];
""", always=True, notes="kept as a node so you can watch what gets filtered in the execution log")
    wf.link(real, skip, 1)
    return wf


# ================================================== 04 FOLLOWUP + REPORT ======
def build_followup() -> WF:
    wf = WF("04-followup-report", "follow-up ladder + portfolio page + nightly numbers")
    a = sched(wf, "Daily 21:00", "REPORT_CRON", notes="after the send windows (REPORT_CRON in .env)")
    lst = crm_list(wf, "Read CRM", after="Daily 21:00")
    stage = code(wf, "Stage follow-ups", """
// the ladder: contacted -> nudge(d2) -> value(d5) -> close(d10); interested+unpaid -> reminder(+1d)
const rows = (($input.first().json || {}).rows) || [];
const today = new Date().toISOString().slice(0, 10);
const MAXF = parseInt($env.MAX_FOLLOWUPS || '2', 10);
const SKIP = ['not_interested', 'paid', 'payment_claimed', 'closed_noreply', 'demo_failed',
              'inbound_unmatched', 'auto_reply', 'spam'];
const out = [];
for (const r of rows) {
  const st = String(r.status || '');
  const days = Number(r._days_since_out !== undefined ? r._days_since_out : 9999);
  const fu = Number(r.followup_count || 0);
  if (!r.lead_key || SKIP.indexOf(st) >= 0) continue;
  if (!r.whatsapp && !r.phone) continue;
  if (st === 'contacted' || st === 'replied' || st === 'negotiating' || st === 'revision_requested'
      || st === 'followup_sent' || st === 'stalled') {
    if (fu >= MAXF) {
      out.push({ json: { op: 'upsert', row_id: r.row_id, lead_key: r.lead_key, business_name: r.business_name,
        status: 'closed_noreply', demo_url: r.demo_url,
        notes: (String(r.notes || '') + ' | auto-closed after ' + fu + ' follow-ups').slice(-1500),
        next_action_at: '', _close_only: true } });
      continue;
    }
    let sg = '';
    if (days >= 2 && fu === 0) sg = 'nudge';
    else if (days >= 5 && fu >= 1) sg = 'value';
    else if (days >= 10) sg = 'close';
    if (!sg) continue;
    out.push({ json: { ...r, _fu: sg } });
    continue;
  }
  if (st === 'interested' && String(r.paid || 'no') !== 'yes') {
    const since = Number(r._days_since_in || 0);
    if (since >= 1 && fu === 0) { out.push({ json: { ...r, _fu: 'payment_reminder' } }); continue; }
    if (since >= 7) out.push({ json: { op: 'upsert', row_id: r.row_id, lead_key: r.lead_key,
      business_name: r.business_name, status: 'stalled', demo_url: r.demo_url,
      notes: (String(r.notes || '') + ' | bola interested, payment nahi aaya').slice(-1500),
      next_action_at: today, _close_only: true } });
    continue;
  }
  if ((st === 'demo_ready' || st === 'queued') && !r.demo_url) {
    out.push({ json: { op: 'upsert', row_id: r.row_id, lead_key: r.lead_key, business_name: r.business_name,
      status: 'demo_failed', notes: (String(r.notes || '') + ' | demo_url missing -> 01 rerun').slice(-1500),
      _close_only: true } });
  }
}
if (!out.length) return [{ json: { empty: true } }];
return out;
""", always=True)
    wf.chain(lst, stage)
    has = ifs(wf, "Kuch pending?", "{{ $json.empty !== true }}")
    wf.chain(stage, has)
    loop = sib(wf, "Per follow-up")
    wf.link(has, loop, 0)
    onlyclose = ifs(wf, "Send message?", "{{ $json._close_only !== true }}",
                    notes="close-only rows are recorded, never messaged")
    wf.link(loop, onlyclose)
    fm = gw(wf, "AI: follow-up", "/v1/ai/chat", {"prompt": "followup", "fast": "true",
        "vars": x("{{ JSON.stringify({ stage: $('Per follow-up').item.json._fu, "
                  "business_name: $('Per follow-up').item.json.business_name, category_label: "
                  "$('Per follow-up').item.json.category_label, city: $('Per follow-up').item.json.city, "
                  "area: $('Per follow-up').item.json.area, demo_url: $('Per follow-up').item.json.demo_url, "
                  "price_label: $('Per follow-up').item.json.price_label, days_since_contact: "
                  "String($('Per follow-up').item.json._days_since_out||0), language: "
                  "$('Per follow-up').item.json.language||'auto', pay_methods: $env.PAY_METHODS, "
                  "offer_text: $env.OFFER_TEXT, previous_messages: String($('Per follow-up').item.json.notes||'')"
                  ".slice(-500), delivery_time_text: $env.DELIVERY_TIME_TEXT, agency_name: $env.AGENCY_NAME, "
                  "owner_name: $env.AGENCY_NAME, user: 'Write exactly this one follow-up.' }) }}")},
              notes="prompts/followup.md decides nudge/value/close/payment_reminder", retry=1,
              onError="continueErrorOutput")
    wf.link(onlyclose, fm, 0)
    gate = code(wf, "Sanity", """
const lead = $('Per follow-up').item.json;
const j = $input.first().json || {};
let m = String(j.text || j.message || '').trim().replace(/```/g, '');
const BANNED = /(dear sir|guaranteed|100% free|limited offer|last chance|final notice|only \\d+ slots|\\ud83d\\ude80|\\ud83d\\udcc8)/i;
if (!m || m.length > 420 || BANNED.test(m)) {
  const link = String(lead.demo_url || '');
  m = (lead.business_name || 'aap') + ', ' + (link ? link + '\\n' : '') +
      (String(lead.language || '').indexOf('urdu') >= 0 || ['PK','BD','IN'].includes(String(lead.country||'').toUpperCase())
        ? 'Abhi bhi dekh sakte hain \\u2014 koi charge nahi. Kaam nahi to bas \\u201cnahi\\u201d bol dein.'
        : 'Still there if you want it \\u2014 no charge. If not, just say no.');
}
const phone = String(lead.whatsapp || lead.phone || '').replace(/\\D/g, '');
return [{ json: { chatId: phone ? phone + '@c.us' : '', body: m.slice(0, 420), stage: lead._fu,
                  lead_key: lead.lead_key, row_id: lead.row_id } }];
""", always=True)
    wf.chain(fm, gate)
    snd = waha(wf, "WAHA: sendText", {"session": x("{{ $env.WAHA_SESSION }}"),
                                      "chatId": x("{{ $('Sanity').item.json.chatId }}"),
                                      "text": x("{{ $('Sanity').item.json.body }}")},
                 "/api/sendText", notes="follow-ups go through the same session", onError="continueErrorOutput")
    wf.link(gate, snd)
    rec = code(wf, "Record follow-up", """
const lead = $('Per follow-up').item.json;
const gate = $('Sanity').item.json;
const r = $input.first().json || {};
const ok = !r.error && (r.id || r.messageId);
const closing = gate.stage === 'close';
return [{ json: { op: 'upsert', row_id: lead.row_id || '', lead_key: lead.lead_key,
  business_name: lead.business_name, followup_count: (Number(lead.followup_count) || 0) + 1,
  status: closing ? 'closed_noreply' : (gate.stage === 'payment_reminder' ? 'interested' : 'followup_sent'),
  last_outbound: gate.body, sent_at: ok ? new Date().toISOString().slice(0, 10) : (lead.sent_at || ''),
  demo_url: lead.demo_url, outbound_count: (Number(lead.outbound_count) || 0) + (ok ? 1 : 0),
  notes: (String(lead.notes || '') + ' | ' + gate.stage + (ok ? ' sent' : ' FAILED')).slice(-1500),
  next_action_at: closing ? '' : new Date(Date.now() + 3 * 864e5).toISOString().slice(0, 10) } }];
""", always=True)
    wf.chain(snd, rec)
    close_rec = code(wf, "Record close", """
const j = $input.first().json || {};
return [{ json: j }];
""", always=True)
    wf.link(onlyclose, close_rec, 1)
    both = code(wf, "Merge", """
// join the two branches so the delay/loop has one input
return $input.all().map(i => ({ json: { op: 'upsert', ...(i.json || {}) } }));
""", always=True)
    wf.link(rec, both)
    wf.link(close_rec, both)
    sv = crm_upsert(wf, "CRM: save", after="Merge")
    nap = wait(wf, "Delay", "{{ 150 + Math.floor(Math.random() * 200) }}",
               notes="follow-ups are outreach too \\u2014 keep the pace human")
    for e in sv:
        wf.link(e, nap)
    wf.link(nap, loop)
    rep = code(wf, "Metrics", """
// the whole dashboard in one message. Numbers you can act on, not vanity charts.
const rows = ($('Read CRM').item.json || {}).rows || [];
const n = s => rows.filter(r => String(r.status || '') === s).length;
const touched = rows.filter(r => ['contacted','replied','interested','negotiating','paid','followup_sent',
  'revision_requested','stalled','closed_noreply','payment_claimed','send_failed']
  .includes(String(r.status || ''))).length;
const replied = rows.filter(r => (Number(r.reply_count) || 0) > 0).length;
const paid = rows.filter(r => String(r.paid || 'no') === 'yes').length;
const ready = rows.filter(r => r.demo_url).length;
const intents = {};
for (const r of rows) if (r.intent) intents[r.intent] = (intents[r.intent] || 0) + 1;
const rate = touched ? Math.round(replied / touched * 100) : 0;
const hot = rows.filter(r => ['interested','negotiating','payment_claimed','handoff','revision_requested']
  .includes(String(r.status || ''))).slice(0, 8)
  .map(r => '\\u2022 ' + (r.business_name || '?') + ' [' + r.status + '] ' + (r.whatsapp || r.phone || ''));
const fails = rows.filter(r => r.status === 'demo_failed').length
            + rows.filter(r => r.status === 'send_failed').length;
return [{ json: {
  total: rows.length, ready, touched, replied, rate, interested: n('interested'),
  negotiating: n('negotiating'), payment_claimed: n('payment_claimed'), paid, dead: n('not_interested'),
  fails, revenue: paid + ' \\u00d7 ' + ($env.PRICE_LABEL || 'price'),
  intents: Object.entries(intents).map(([k, v]) => k + ':' + v).join('  '),
  todo: hot.join('\\n') || '(aaj koi reply nahi \\u2014 wording badalne ka waqt)',
  advice: rate < 12 && touched > 8 ? 'Reply rate ' + rate + '% \\u2192 pehli line kamzor hai. Safety gate '
      + rows.filter(r => String(r.ai_notes||'').includes('fallback')).length + ' baar fallback use kar raha tha.'
    : touched < 12 ? 'Volume kam \\u2192 config/campaigns me areas barhayein ya MAX_LEADS_PER_DAY badhayein'
    : n('interested') > 0 && paid === 0 ? 'Interested bohat, paid zero \\u2192 payment ke baad ka step '
        'aur fast karein (site 2 ghante me live)'
    : 'Funnel theek \\u2192 PRICE_LABEL test karein (+20-30% aksar chalega)',
  followups: rows.filter(r => r.status === 'followup_sent').length } }];
""", always=True)
    wf.link(loop, rep, 1)
    wf.link(has, rep, 0)
    port = gw(wf, "Portfolio page", "/v1/site/portfolio", {"rows_json": x(
        "{{ JSON.stringify((($('Read CRM').item.json || {}).rows || []).filter(r => r.demo_url)"
        ".slice(0, 60).map(r => ({demo_url: r.demo_url, title: r.business_name, "
        "category_label: r.category_label, city: r.city, emoji: r.emoji || '\\ud83c\\udfea'}))) }}")},
        notes="aapki demos ki public gallery \\u2014 sabse sasta sales tool", retry=1,
        onError="continueRegularOutput", always=True)
    wf.link(rep, port)
    note = code(wf, "Attach portfolio", """
const p = $input.first().json || {};
const m = $('Metrics').item.json;
return [{ json: { ...m, portfolio: p.url || (p.error ? 'skipped' : '') } }];
""", always=True)
    wf.chain(port, note)
    tg3 = tg(wf, "Telegram: daily report", """{{
  "<b>\\ud83d\\udcca Daily \\u2014 " + $json.total + " leads (CRM)</b>\\n" +
  "demos live: " + $json.ready + "\\nbheje: " + $json.touched + "\\nreplies: " + $json.replied +
  " (" + $json.rate + "%)\\n" +
  "interested: " + $json.interested + " | negotiating: " + $json.negotiating +
  " | paid-claim: " + $json.payment_claimed + "\\n\\ud83d\\udcb0 paid: " + $json.paid + "  (" + $json.revenue + ")" +
  "\\ndeclined: " + $json.dead + " | failures: " + $json.fails +
  ($json.intents ? "\\nintents: " + $json.intents : "") +
  "\\n\\n<b>\\ud83d\\udea6 Aaj/kaal ka kaam:</b>\\n" + $json.todo +
  "\\n\\n\\ud83d\\udca1 " + $json.advice +
  ($json.portfolio ? "\\n\\ud83d\\uddbc portfolio: " + $json.portfolio : "")
}}""", notes="roz raat 9 baje \\u2014 yahi aapka poora business dashboard hai")
    wf.chain(note, tg3)
    return wf


# ================================================= 05 VOICE NOTES (OPTIONAL) =
def build_voice() -> WF:
    """The free substitute for cold calling: an AI voice note on WhatsApp.
    POST {phone, business_name, lead_key} to /webhook/voice-note (from Telegram link, a button,
    or workflow 03 when someone says 'call karo')."""
    wf = WF("05-voice-note-optional", "OPTIONAL: AI voice note on WhatsApp (calling ka free substitute)")
    hook = wf.add("Request", HOOK, 2, {"httpMethod": "POST", "path": "voice-note",
                                       "responseMode": "responseNode", "options": {}},
                  notes='curl -X POST localhost:5678/webhook/voice-note -d \'{"phone":"92300...",'
                        '"business_name":"Bismillah Bakers"}\'')
    ack = wf.add("Done", RESPOND, 1.1, {"respondWith": "json",
                                        "responseBody": "={{ JSON.stringify($json) }}",
                                        "options": {"responseCode": 200}}, always=True)
    script = gw(wf, "AI: 35-word spoken script", "/v1/ai/chat", {"prompt": "voicenote",
        "vars": x("{{ JSON.stringify({ business_name: $json.business_name || 'aapki dukaan', "
                  "category_label: $json.category_label || 'business', city: $json.city || 'Karachi', "
                  "demo_url: $json.demo_url || '', offer_text: $env.OFFER_TEXT, price_label: "
                  "$env.PRICE_LABEL, agency_name: $env.AGENCY_NAME, owner_name: $env.AGENCY_NAME, "
                  "language: $json.language || 'roman-urdu', user: 'Write the spoken script now.' }) }}")},
        notes="spoken style != written style: short sentences, no numbers longer than 4 digits",
        retry=2, onError="continueErrorOutput")
    tts = http(wf, "TTS: make audio", x("{{ 'http://voicenote:8090/tts' }}"), method="POST",
               body={"text": x("{{ $('AI: 35-word spoken script').item.json.text }}"),
                     "voice": x("{{ $json.voice || '' }}")},
               notes="docker compose --profile voicenote up -d  (free: edge/gtts/piper)",
               onError="continueErrorOutput", retry=1)
    send = http(wf, "WAHA: sendVoice", x("{{ $env.WAHA_URL + '/api/sendVoice' }}"), method="POST",
                headers={"Content-Type": "application/json",
                         "X-Api-Key": x("{{ $env.WAHA_API_KEY }}")},
                body=x("{{ JSON.stringify({ session: $env.WAHA_SESSION, chatId: String($('Request')"
                       ".item.json.phone || '').replace(/[^0-9]/g,'') + '@c.us', file: { url: "
                       "$('TTS: make audio').item.json.url, fileName: $('TTS: make audio')"
                       ".item.json.filename } }) }}"),
                onError="continueErrorOutput",
                notes="nested file.url needs a JSON body (not keypair); WAHA fetches it inside the net")
    wf.chain(hook, script, tts, send, ack)
    err = code(wf, "Explain failure", """
const j = $input.first().json || {};
return [{ json: { ok: false, error: String(j.error || j.message || 'failed').slice(0, 400),
                  hint: j.error ? 'check: docker compose --profile voicenote up -d ; GATEWAY_URL; WAHA session' : '' } }];
""", always=True)
    wf.link(script, err, 1)
    wf.link(tts, err, 1)
    wf.link(send, err, 1)
    wf.link(err, ack)
    return wf


def main() -> int:
    builders = [build_leads, build_outreach, build_replies, build_followup, build_voice]
    total = 0
    for b in builders:
        wf = b()
        p = wf.write()
        kb = len(json.dumps(wf.json())) / 1024
        total += len(wf.nodes)
        print(f"  wrote n8n/workflows/{p.name:<26} {len(wf.nodes):>3} nodes, "
              f"{len(wf.edges):>3} links, {kb:5.1f} KB")
    print(f"total nodes: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
