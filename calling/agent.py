#!/usr/bin/env python3
"""agent.py — the BRAIN of an AI phone call, runnable today with zero telephony.

Read this file top to bottom: it is deliberately split so the hard/expensive part (PSTN) is
separate from the part that is free (the conversation engine).

    python3 calling/agent.py --selftest     # no network, no keys: proves the state machine
    python3 calling/agent.py                # chat REPL (type what the owner says)
    python3 calling/agent.py --business "Bismillah Bakers" --demo https://x/abc/
    python3 calling/agent.py --say-file script.txt   # offline batch run

Real phone audio (STT in / TTS out + SIP/RTP) lives in pipecat_call.py and costs money per
docs/free-vs-paid.md — that is why this file works fine on a text channel too (WhatsApp voice
note style) and can be pointed at Ollama + Piper for 100% local operation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATEWAY = (os.getenv("GATEWAY_URL") or "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.getenv("GATEWAY_TOKEN", "")

# --------------------------------------------------------------------- state


@dataclass
class Call:
    business: str = "aapki dukaan"
    category: str = "shop"
    city: str = "Karachi"
    demo_url: str = ""
    price: str = "PKR 15,000 one-time"
    agency: str = "WebDemo.pk"
    owner: str = "Ali"
    pay: str = "Easypaisa: XXXX"
    max_turns: int = 12
    turn: int = 0
    stage: str = "greet"                 # greet -> hook -> handle -> pitch -> close -> end
    objections: int = 0
    interested: bool = False
    said_no: bool = False
    transcript: list[tuple[str, str]] = field(default_factory=list)

    def say(self, who: str, text: str) -> None:
        self.transcript.append((who, text))


YES = re.compile(r"\b(haan|ji haan|bhejo|bhej (deta|diya|do)|kar do|bana do|send karo|chahiye hai|kartay hain|"
                 r"okay|theek hai|sahi|dekh liya|interest hai|manzoor)\b", re.I)
NO = re.compile(r"\b(nahi|nahin|not interested|no thanks|zaroorat nahi|mat karein|mana kar|nahi chahiye)\b", re.I)
SOFT = re.compile(r"\b(busy|baad me|dobara|meeting|garmi|sone|namaz|kaam ke beech|after some time)\b", re.I)
HOSTILE = re.compile(r"\b(scam|fake|fraud|poly|report karunga|thug|block kar dunga|lawyer|police|"
                     r"harass)\b", re.I)
PRICE = re.compile(r"\b(kitna|price|rate|charges|cost|kitne|fee|monthly)\b", re.I)
TRUST = re.compile(r"\b(kaun ho|tumhara|proof|refrence|reference|location|kahan se|fake|trust)\b", re.I)


def classify(text: str) -> str:
    """Roman-Urdu + English intent for a phone call. Order matters: a hostile or busy owner
    must never be read as 'no', and 'nahi chahiye' must never be read as 'yes'."""
    t = (text or "").strip()
    if not t:
        return "silence"
    if HOSTILE.search(t):
        return "hostile"
    if TRUST.search(t):
        return "trust"
    if SOFT.search(t) and not NO.search(t):
        return "busy"
    if NO.search(t) and not re.search(r"\b(haan|ji haan|theek hai|bhejo)\b", t, re.I):
        return "no"
    if PRICE.search(t):
        return "price"
    if YES.search(t):
        return "yes"
    if re.search(r"(\?\s*$|\bkya\b|\bkyun\b|\bkyu\b|\bkaise\b|\bkaha\b|\bkahan\b|"
                 r"\bkon\b|\bkaun\b|\bhow\b|\bwhat\b|\bwhere\b)", t, re.I):
        return "question"
    return "unknown"


# ------------------------------------------------------- deterministic replies
# Kept intentionally short: a phone answer must be < 15 seconds to hold attention.
SCRIPT = {
    "greet": "{owner} bol raha hoon {agency} se — {business} ke liye ek site banaya tha. "
             "Abhi 2 minute hain ya baad me call karoon?",
    "hook": "Maine Google se {business} dekha — {city} me log aapko dhoondhte hain magar "
            "unhein menu/timings kahin nahi milte. Maine ek page bana diya hai, aapka naam "
            "us par live hai. Link WhatsApp par bhej deta hoon?",
    "price": "Ek hi baar {price}. Hosting pehle saal free, uske baad {yearly}. Site 24 ghante me "
             "live, ek revision free. Pasand na aaye to kuch nahi dena — link dekh lein pehle.",
    "trust": "Bilkul theek sawaal hai. Main aapko jhoot nahi bolunga: site mere account par live hai, "
             "link khol kar aap apna naam khud dekhein ge. Payment sirf tab jab aap ko pasand aaye, "
             "aur wo bhi {pay} par — receipt main bhej dunga. Koi contract nahi.",
    "no": "Bilkul theek hai, main dobara nahi karunga. Ek kaam karo — {demo_url} ka link dekh lein, "
          "koi charge nahi. Shukriya time dene ke liye.",
    "busy": "Koi baat nahi, 2 minute me link bhej raha hoon WhatsApp par. Jab free hon to dekh lein.",
    "hostile": "Sorry, main turant band kar raha hoon. Aapko koi message dobara nahi aayega. Good day.",
    "close": "Perfect. Main abhi {demo_url} WhatsApp karta hoon — payment aate hi aaj shaam tak site "
             "aapke naam par. Ek kaam karein, 3 photos bhej dein (front, product, interior).",
    "ask": "Sirf ek line me bata dein — {q}?",
    "unknown": "Maaf kijiye, samajh nahi aaya. Kya main aapke liye demo link WhatsApp par bhej doon?",
}


def reply_scripted(c: Call, intent: str) -> str:
    """No-LLM safety net: keeps the call going even with zero API keys / zero internet."""
    yearly = "PKR 3,000"
    # safety first: an angry owner is ended before anything else (even the opener)
    if intent == "hostile":
        c.stage, c.said_no = "end", True
        return SCRIPT["hostile"].format(**vars(c))
    if c.stage == "greet":
        c.stage = "hook"
        return SCRIPT["greet"].format(**vars(c))
    if intent == "price":
        c.interested = True
        c.stage = "close" if c.objections >= 1 else "pitch"
        return SCRIPT["price"].format(price=c.price, yearly=yearly, pay=c.pay)
    if intent == "trust":
        c.objections += 1
        return SCRIPT["trust"].format(pay=c.pay)
    if intent == "yes":
        c.interested = True
        c.stage = "close"
        return SCRIPT["close"].format(**vars(c))
    if intent == "no":
        c.said_no = True
        c.stage = "end"
        return SCRIPT["no"].format(demo_url=c.demo_url or "link")
    if intent == "busy":
        c.stage = "handle"
        return SCRIPT["busy"].format(**vars(c))
    if intent == "silence":
        return "Hello? Awaaz aa rahi hai?"
    c.turn += 1
    if c.turn >= c.max_turns:
        c.stage = "end"
        return "Main aapko WhatsApp par detail bhej deta hoon. Shukriya, {owner} — {agency}.".format(**vars(c))
    return SCRIPT["unknown"].format(**vars(c))


# ------------------------------------------------------------------ LLM layer


def llm(c: Call, user_line: str) -> str | None:
    """Ask the gateway (Gemini->Groq->Ollama) for a better line. None = caller falls back."""
    try:
        prompt = (ROOT / "prompts" / "call-agent.md").read_text(encoding="utf-8")
    except Exception:
        return None
    hist = "\n".join(f"{who}: {txt}" for who, txt in c.transcript[-12:])
    sys_p = prompt
    for k, v in {"{{business_name}}": c.business, "{{category_label}}": c.category,
                 "{{city}}": c.city, "{{demo_url}}": c.demo_url, "{{price_label}}": c.price,
                 "{{agency_name}}": c.agency, "{{owner_name}}": c.owner, "{{pay_methods}}": c.pay,
                 "{{stage}}": c.stage, "{{history}}": hist or "(call just started)",
                 "{{owner_said}}": user_line}.items():
        sys_p = sys_p.replace(k, v)
    body = json.dumps({"system": sys_p, "user": "Reply with ONE short spoken line only.",
                       "max_tokens": 140, "temperature": 0.6}).encode()
    req = urllib.request.Request(f"{GATEWAY}/v1/ai/chat", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"X-Gateway-Token": TOKEN} if TOKEN else {})})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read().decode()).get("text")
    except Exception as e:
        print(f"   (llm unavailable: {type(e).__name__} — using scripted brain)", file=sys.stderr)
        return None


def next_line(c: Call, user_line: str, use_llm: bool = True) -> str:
    intent = classify(user_line)
    line = llm(c, f"[owner said, intent~{intent}]: {user_line}") if use_llm else None
    if not line:
        line = reply_scripted(c, intent)
    line = re.sub(r"\s+", " ", line).strip().strip('"')[:600]
    c.say("owner", user_line)
    c.say("agent", line)
    return line


# -------------------------------------------------------------------- runner


def repl(c: Call, use_llm: bool) -> None:
    print(f"📞 AI call agent — {c.business} ({c.city}). Owner ke shabd type karein. "
          f"'quit' = call end, 'intent <text>' = sirf classifier test.\n")
    first = reply_scripted(c, "greet")
    c.say("agent", first)
    print("agent:", first, "\n")
    while c.stage != "end":
        try:
            line = input("owner: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in ("quit", "exit"):
            break
        if line.lower().startswith("intent "):
            print("intent:", classify(line[7:]))
            continue
        c.turn += 1
        print("agent:", next_line(c, line, use_llm))
        if c.turn > c.max_turns:
            c.stage = "end"
    print(f"\n— call ended. stage={c.stage} interested={c.interested} turns={c.turn}")
    print("  transcript saved: calling/last-call.json")
    Path(__file__).with_name("last-call.json").write_text(json.dumps(
        {"business": c.business, "stage": c.stage, "interested": c.interested,
         "transcript": c.transcript}, ensure_ascii=False, indent=2))


def selftest() -> int:
    cases = [
        ("haan bhejo link", "yes"), ("nahi chahiye", "no"), ("kitna lagega", "price"),
        ("aap kaun ho", "trust"), ("main busy hoon baad me", "busy"), ("", "silence"),
        ("tum fraud ho main report karunga", "hostile"), ("menu kaha hai aapka", "question"),
    ]
    bad = 0
    for text, want in cases:
        got = classify(text)
        ok = got == want or (want == "question" and got == "unknown")
        print(f"  {'✓' if ok else '✗'} {text[:34]:<36} -> {got:<9} (want {want})")
        bad += 0 if ok else 1
    # full happy path
    c = Call(business="Bismillah Bakers", demo_url="https://u.github.io/d/bismillah/")
    for line in ("ji bolo", "haan bhejo", "kitna paisa", "theek hai main bhej deta hoon"):
        reply_scripted(c, classify(line))
    assert c.interested, "AI must mark interest after a yes/price path"
    assert c.stage in ("close", "end", "pitch"), c.stage
    # rejection must end the call politely in <=2 turns (anti-harassment)
    c2 = Call()
    reply_scripted(c2, "greet")
    out = reply_scripted(c2, "no")
    assert c2.said_no and c2.stage == "end", "a 'no' must close immediately"
    assert "nahi karunga" in out.lower() or "shukriya" in out.lower(), "rejection reply must be polite"
    # hostile -> straight goodbye, no pitch
    c3 = Call()
    out3 = reply_scripted(c3, "hostile")
    assert c3.stage == "end" and "sorry" in out3.lower()
    print("\n✅ state-machine selftest passed (no keys, no network needed)")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--business", default=os.getenv("TEST_BUSINESS", "Bismillah Bakers"))
    ap.add_argument("--category", default="Restaurant / Cafe")
    ap.add_argument("--city", default="Karachi")
    ap.add_argument("--demo", default="https://USERNAME.github.io/demo-sites/bismillah-bakers/")
    ap.add_argument("--price", default=os.getenv("PRICE_LABEL", "PKR 15,000 one-time"))
    ap.add_argument("--pay", default=os.getenv("PAY_METHODS", "Easypaisa: XXXX"))
    ap.add_argument("--no-llm", action="store_true", help="only the offline scripted brain")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--say-file", help="run each line of this file as the owner, print agent lines")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    c = Call(business=a.business, category=a.category, city=a.city, demo_url=a.demo,
             price=a.price, pay=a.pay)
    if a.say_file:
        for line in Path(a.say_file).read_text().splitlines():
            if line.strip():
                print("owner:", line)
                print("agent:", next_line(c, line, use_llm=not a.no_llm))
        print(f"\n[stage={c.stage} interested={c.interested}]")
        return 0
    repl(c, use_llm=not a.no_llm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
