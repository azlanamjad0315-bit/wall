#!/usr/bin/env python3
"""pipecat_call.py — EXPERIMENTAL: real phone audio for the agent above.

⚠️ READ docs/free-vs-paid.md FIRST. The code below is free; the PHONE NUMBER and the minutes
are not. Nothing in this file costs money until you plug in a PSTN carrier (Twilio/Telnyx/Vonage).

Two ways to use it:
  A) TEXT MODE (free, now, no carrier):   python3 calling/pipecat_call.py --text
     -> runs agent.py's brain through pipecat's pipeline plumbing, stdin/stdout.
  B) PHONE MODE (needs a SIP trunk):      python3 calling/pipecat_call.py --sip

Dependencies (pin these — pipecat's class names move fast between releases):
    pip install "pipecat-ai[silero,standard]" pipecat-ai-together   # not needed for --text
    pip install faster-whisper piper-tts ollama                     # local STT/TTS/LLM
Every class import below is inside a try so `--text` still runs if pipecat is missing.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import Call, classify, next_line  # the brain (tested, offline-capable)

OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")       # "small" = best Urdu/English mix on CPU
PIPER_MODEL = os.getenv("PIPER_MODEL", "en_US-lessac-medium")


async def listen_once(faster_whisper=None) -> str:     # mic -> text (offline)
    """Record from the default mic, VAD-end the utterance, transcribe locally with faster-whisper.
    Kept tiny on purpose: on a real call pipecat's transport does this for you."""
    try:
        import numpy as np
        import sounddevice as sd
        import wave
    except Exception as e:
        print("mic deps nahi (sounddevice/numpy):", e, file=sys.stderr)
        return ""
    sr, frames = 16000, []
    print("🎙️  bolo (silence = band)...")
    with sd.RawInputStream(samplerate=sr, blocksize=2048, dtype="int16", channels=1) as st:
        import time
        t0 = time.time()
        last_voice = time.time()
        while time.time() - t0 < 25:
            data, avail = st.read(2048)
            if avail:
                frames.append(bytes(data))
                peak = abs(np.frombuffer(bytes(data), dtype=np.int16)).max() if len(data) else 0
                if peak > 700:
                    last_voice = time.time()
                if time.time() - last_voice > 1.1 and frames:
                    break
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(frames))
    buf.seek(0)
    if faster_whisper is None:
        try:
            from faster_whisper import WhisperModel
            faster_whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        except Exception as e:
            print("faster-whisper nahi chala:", e, "-> text mode me switch", file=sys.stderr)
            return input("owner bola (type): ").strip()
    segs, _ = faster_whisper.transcribe(buf, language=None, vad_filter=True)
    return " ".join(s.text for s in segs).strip()


def speak(text: str) -> None:
    """text -> speech. Tries the free voicenote service, then piper CLI, then just prints."""
    import json
    import urllib.request
    url = os.getenv("VOICENOTE_URL", "http://127.0.0.1:8090/tts")
    try:
        req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read().decode())
        print(f"🔊 [{j.get('backend')}] {text}")
        if j.get("path") and os.path.exists(j["path"]):
            os.system(f"ffplay -nodisp -autoexit -loglevel quiet '{j['path']}' 2>/dev/null || true")
        return
    except Exception:
        pass
    print(f"agent: {text}")


async def run_text_call(business: str, demo: str) -> None:
    c = Call(business=business, demo_url=demo,
             price=os.getenv("PRICE_LABEL", "PKR 15,000 one-time"),
             pay=os.getenv("PAY_METHODS", "Easypaisa: XXXX"))
    speak(next_line(c, "", use_llm=True))
    for _ in range(c.max_turns):
        line = await listen_once()
        if not line or line.lower() in ("quit", "bas"):
            break
        speak(next_line(c, line, use_llm=True))
        if c.stage == "end":
            break
    print(f"\n— intent={classify(c.transcript[-2][1] if len(c.transcript) > 1 else '')} "
          f"interested={c.interested} refused={c.said_no}")
    Path(__file__).with_name("last-call.json").write_text(
        __import__("json").dumps({"business": c.business, "interested": c.interested,
                                  "transcript": c.transcript}, ensure_ascii=False, indent=2))


async def run_sip_call() -> None:
    """Real inbound/outbound SIP. Left explicit + guarded so you can see exactly what to install."""
    try:
        from pipecat.pipeline.pipeline import Pipeline           # noqa
        from pipecat.transports.network.small_websocket import SmallWebsocketTransport  # noqa
        from pipecat.serializers.twilio import TwilioFrameSerializer                   # noqa
        from pipecat.services.faster_whisper import FasterWhisperSTTService           # noqa
        from pipecat.services.ollama import OllamaLLMService                          # noqa
        from pipecat.services.piper import PiperTTSService                            # noqa
    except Exception as e:
        print(
            "\n❌ pipecat imports fail (class names move between versions). Pin pipecat-ai to the\n"
            "   version in your lockfile and read the current example:\n"
            "     https://github.com/pipecat-ai/pipecat/blob/main/examples/twilio-chatbot/\n"
            f"   ({e})\n\n"
            "   Aur sabse pehle: ek SIP/PSTN number chahiye — wo PAID hai. docs/free-vs-paid.md\n",
            file=sys.stderr)
        return
    print("pipecat imports OK — ab apni trunk ke credentials se pipeline wire karein "
          "(Twilio number + SIP domain + webhook -> n8n/FastAPI). Sample flow upar --text mode me hai.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true", help="free: mic/typing brain test, no telephony")
    ap.add_argument("--sip", action="store_true", help="real call (needs a PAID SIP trunk)")
    ap.add_argument("--business", default=os.getenv("TEST_BUSINESS", "Bismillah Bakers"))
    ap.add_argument("--demo", default="https://USERNAME.github.io/demo-sites/demo/")
    a = ap.parse_args()
    if a.sip:
        asyncio.run(run_sip_call())
        return 0
    asyncio.run(run_text_call(a.business, a.demo))
    return 0


if __name__ == "__main__":
    main()
