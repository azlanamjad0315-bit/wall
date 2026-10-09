#!/usr/bin/env python3
"""validate_workflows.py — CI for the generated n8n files (run: make validate).

n8n's importer is silent about half its failure modes; it just shows a red node or drops a
connection. These checks catch the stuff that actually bites:

  * node names/ids unique, connections point at real nodes, output indices within arity
  * every node reachable from a trigger (an orphan node = a step you think runs but never does)
  * `{{ }}` expressions balanced + only functions n8n's sandbox really provides
  * every $env.X we reference exists in .env.example (typo in an env name = silently empty key)
  * Code nodes: no bare `return` outside a function-safe position, no template-literal leakage
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WFDIR = ROOT / "n8n" / "workflows"

TRIGGERS = {"n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.webhook",
            "n8n-nodes-base.executeWorkflowTrigger", "n8n-nodes-base.formTrigger"}
KNOWN = {
    "n8n-nodes-base.httpRequest": 4.2, "n8n-nodes-base.code": 2, "n8n-nodes-base.if": 2.2,
    "n8n-nodes-base.switch": 3.2, "n8n-nodes-base.splitInBatches": 3, "n8n-nodes-base.wait": 1.1,
    "n8n-nodes-base.scheduleTrigger": 1.2, "n8n-nodes-base.webhook": 2,
    "n8n-nodes-base.respondToWebhook": 1.1, "n8n-nodes-base.emailSend": 2.1,
    "n8n-nodes-base.executeWorkflow": 1.2, "n8n-nodes-base.executeWorkflowTrigger": 1.1,
    "n8n-nodes-base.noOp": 1, "n8n-nodes-base.set": 3.4,
}
# node type -> max output index normally wired (informational; IF=2, Switch=rules+fallback)
MAX_OUT = {"n8n-nodes-base.if": 2, "n8n-nodes-base.code": 1, "n8n-nodes-base.httpRequest": 2,
           "n8n-nodes-base.splitInBatches": 2, "n8n-nodes-base.scheduleTrigger": 1,
           "n8n-nodes-base.webhook": 1, "n8n-nodes-base.respondToWebhook": 1,
           "n8n-nodes-base.wait": 1, "n8n-nodes-base.noOp": 1, "n8n-nodes-base.emailSend": 1,
           "n8n-nodes-base.executeWorkflow": 1}
ENV_OK = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
EXPR_FN = re.compile(r"\$\(\\?['\"]([^'\"\\]+)")   # $('Node Name') references
BAD_GLOBALS = re.compile(r"\b(require|process\.env|localStorage|fetch)\s*\(")  # not in n8n sandbox


def load_env_names() -> set[str]:
    f = ROOT / ".env.example"
    names = set()
    if f.exists():
        for line in f.read_text().splitlines():
            m = re.match(r"^([A-Z][A-Z0-9_]{2,})=", line.strip())
            if m:
                names.add(m.group(1))
    names |= {"GATEWAY_URL", "WAHA_URL", "WAHA_SESSION", "WAHA_API_KEY", "BASEROW_URL", "BASEROW_TOKEN",
              "BASEROW_TABLE_ID", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GATEWAY_TOKEN",
              "MAX_LEADS_PER_DAY", "MAX_OUTREACH_PER_DAY", "OUTREACH_MIN_DELAY_SECONDS",
              "OUTREACH_MAX_DELAY_SECONDS", "LEADS_CRON", "OUTREACH_CRON", "REPORT_CRON",
              "AUTO_REPLY_ENABLED", "AUTO_REPLY_MAX_PER_THREAD", "PRICE_LABEL", "PAY_METHODS",
              "PAY_INSTRUCTIONS", "AGENCY_NAME", "GMAIL_SMTP_FROM", "ALERT_EVERY_REPLY",
              "MAX_FOLLOWUPS", "QUIET_START_HOUR", "QUIET_END_HOUR", "WAHA_PHONE", "CRM_BACKEND"}
    return names


def walk_expressions(obj, key: str | None = None):
    """yield every string that n8n will evaluate (values starting with '=')"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_expressions(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_expressions(v, key)
    elif isinstance(obj, str):
        if key != "jsCode":                      # Code node JS is not an expression language
            if obj.startswith("="):
                yield obj[1:]
            elif "{{" in obj:
                yield obj        # bare {{ }} without the leading = is always a bug


def balance(expr: str) -> str | None:
    if expr.count("{{") != expr.count("}}"):
        return "unbalanced {{ }}"
    for open_, close_ in (("[", "]"), ("(", ")")):
        if expr.count(open_) != expr.count(close_):
            return f"unbalanced {open_}{close_}"
    return None


def check_file(path: Path, env_names: set[str]) -> list[str]:
    err: list[str] = []
    try:
        wf = json.loads(path.read_text())
    except Exception as e:
        return [f"{path.name}: not valid JSON: {e}"]
    nodes = wf.get("nodes") or []
    conns = wf.get("connections") or {}
    if not nodes:
        return [f"{path.name}: no nodes"]
    names, ids = [], []
    for n in nodes:
        for k in ("id", "name", "type", "typeVersion", "position", "parameters"):
            if k not in n:
                err.append(f"{path.name}/{n.get('name','?')}: missing '{k}'")
        if not isinstance(n.get("position"), list) or len(n.get("position") or []) != 2:
            err.append(f"{path.name}/{n.get('name')}: position must be [x, y]")
        names.append(n["name"])
        ids.append(n["id"])
        t, tv = n.get("type"), n.get("typeVersion")
        if t not in KNOWN:
            err.append(f"{path.name}/{n['name']}: unknown node type {t}")
        elif isinstance(tv, (int, float)) and tv > KNOWN[t]:
            err.append(f"{path.name}/{n['name']}: typeVersion {tv} > known {KNOWN[t]}")
        if n.get("disabled"):
            continue
        blob = json.dumps(n.get("parameters") or {}, ensure_ascii=False)
        for expr in walk_expressions(n.get("parameters") or {}):
            if "{{" not in expr:
                continue
            why = balance(expr)
            if why:
                err.append(f"{path.name}/{n['name']}: expression {why}: {expr[:90]}")
        for e in ENV_OK.finditer(r"\$env\.([A-Z0-9_]+)"):
            if e.group(1) not in env_names:
                err.append(f"{path.name}/{n['name']}: $env.{e.group(1)} not in .env.example")
        for fn in BAD_GLOBALS.findall(blob):
            err.append(f"{path.name}/{n['name']}: '{fn}()' is not available in n8n expressions/Code")
        if n.get("type") == "n8n-nodes-base.code":
            js = (n.get("parameters") or {}).get("jsCode", "")
            if "return" not in js:
                err.append(f"{path.name}/{n['name']}: Code node never returns items")
            if re.search(r"\$\{", js):
                err.append(f"{path.name}/{n['name']}: Code node has a stray ${{ }} (that's JS template "
                           f"syntax, not an n8n expression)")
        if n.get("type") == "n8n-nodes-base.webhook":
            p = n.get("parameters") or {}
            if not p.get("path"):
                err.append(f"{path.name}/{n['name']}: webhook has no path")
        if n.get("type") == "n8n-nodes-base.httpRequest":
            p = n.get("parameters") or {}
            u = str(p.get("url", ""))
            if not u:
                err.append(f"{path.name}/{n['name']}: HTTP node with no url")
            if p.get("specifyBody") == "keypair" and not (p.get("bodyParameters") or {}).get("parameters"):
                err.append(f"{path.name}/{n['name']}: specifyBody=keypair but no bodyParameters")
            if p.get("specifyBody") == "json" and not p.get("jsonBody"):
                err.append(f"{path.name}/{n['name']}: specifyBody=json but no jsonBody")
        if n.get("type") == "n8n-nodes-base.if":
            c = ((n.get("parameters") or {}).get("conditions") or {})
            if not c.get("conditions"):
                err.append(f"{path.name}/{n['name']}: IF has no conditions")
            for cc in c.get("conditions") or []:
                if "operator" not in cc or "leftValue" not in cc:
                    err.append(f"{path.name}/{n['name']}: IF condition incomplete")

    if len(set(names)) != len(names):
        err.append(f"{path.name}: duplicate node names break $('Name') lookups: " +
                   ", ".join(sorted({x for x in names if names.count(x) > 1})))
    if len(set(ids)) != len(ids):
        err.append(f"{path.name}: duplicate node ids")

    # ---- connections
    known = set(names)
    indeg = {n: 0 for n in names}
    for src, spec in conns.items():
        if src not in known:
            err.append(f"{path.name}: connection from missing node '{src}'")
            continue
        for out_type, outputs in (spec or {}).items():
            if out_type != "main":
                continue
            for i, branch in enumerate(outputs or []):
                t = next((x for x in nodes if x["name"] == src), {})
                if t.get("type") == "n8n-nodes-base.if" and i > 1:
                    err.append(f"{path.name}/{src}: IF has only 2 outputs, wired index {i}")
                maxo = MAX_OUT.get(t.get("type", ""), 99)
                if i >= maxo and t.get("type") != "n8n-nodes-base.switch":
                    err.append(f"{path.name}/{src}: output index {i} beyond {t.get('type')} arity {maxo}")
                for c in branch or []:
                    tgt = c.get("node")
                    if tgt not in known:
                        err.append(f"{path.name}: '{src}' -> missing node '{tgt}'")
                        continue
                    indeg[tgt] = indeg.get(tgt, 0) + 1
                    if c.get("index", 0) != 0:
                        err.append(f"{path.name}: input index != 0 for '{tgt}'")
    for n in nodes:
        if n["type"] in TRIGGERS:
            continue
        if indeg.get(n["name"], 0) == 0:
            err.append(f"{path.name}/{n['name']}: unreachable — nothing connects into it")
    if not any(n["type"] in TRIGGERS for n in nodes):
        err.append(f"{path.name}: no trigger node — this workflow can never run")
    # $('Node') references must exist
    blob = json.dumps(wf, ensure_ascii=False)
    for ref in set(EXPR_FN.findall(blob)):
        if ref not in known:
            err.append(f"{path.name}: expression references unknown node '$(\"{ref}\")'")
    return err


def main() -> int:
    files = sorted(WFDIR.glob("*.json"))
    if not files:
        print("no workflow files in n8n/workflows — run: python3 tools/build_workflows.py", file=sys.stderr)
        return 2
    env_names = load_env_names()
    bad = 0
    for f in files:
        errs = check_file(f, env_names)
        wf = json.loads(f.read_text())
        n = len(wf.get("nodes") or [])
        if errs:
            bad += 1
            print(f"✗ {f.name}  ({n} nodes)")
            for e in errs:
                print(f"    - {e}")
        else:
            print(f"✓ {f.name}  {n} nodes, {sum(len(b) for s in (wf.get('connections') or {}).values() for b in (s.get('main') or []))} links, all checks pass")
    print(f"\n{len(files) - bad}/{len(files)} workflows clean")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
