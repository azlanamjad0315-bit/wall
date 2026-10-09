"""voicenote.py — free offline/neural TTS sidecar.

WHY: real phone calling is not free (PSTN number + per-minute cost, see README.md).
A WhatsApp *voice note* is free, feels as personal as a call, and lands in the same thread as
your demo link. This service turns text into an .mp3 that WAHA can send via /api/sendVoice.

Backend ladder (auto-detected, first one that works wins):
  piper  – fully offline, natural, no network. English voices are excellent; Urdu is weak.
  edge   – Microsoft Edge neural TTS endpoint, free, no key, has ur-PK/hi-IN voices. Needs internet.
  gtts   – Google Translate TTS, free, no key, supports 'ur'. Robotic but always works.
Nothing here costs money and nothing needs an account.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voicenote")

MEDIA = Path(os.getenv("MEDIA_DIR", "/media"))
MEDIA.mkdir(parents=True, exist_ok=True)
PUBLIC = os.getenv("VOICE_PUBLIC_URL", "http://voicenote:8090").rstrip("/")   # WAHA fetches from inside the net
BACKEND = os.getenv("TTS_BACKEND", "auto").lower()
PIPER_MODEL = os.getenv("PIPER_MODEL", "en_US-lessac-medium")
MAX_CHARS = int(os.getenv("TTS_MAX_CHARS", "700"))

app = FastAPI(title="free voice notes", version="1.0")
_cache: dict[str, dict] = {}


# ---------------------------------------------------------------- detectors


def looks_urdu(t: str) -> bool:
    if re.search(r"[\u0600-\u06FF]", t):                  # nastaliq script
        return True
    marks = (" aap ", " hai ", " nahi ", " karein ", " ka ", " ki ", " apki ", " likhein ",
             " salam", " shukriya", " theek ", " kar dein")
    low = " " + t.lower() + " "
    return sum(m in low for m in marks) >= 2


def voice_for(t: str) -> tuple[str, str]:
    """returns (edge_voice_hint, gtts_lang)"""
    if looks_urdu(t):
        return "ur", "ur"
    return "en", "en"


# ------------------------------------------------------------------ backends


def piper_say(text: str, out: Path) -> bool:
    """Offline neural TTS. piper-tts must be installed; model auto-downloaded once."""
    exe = shutil.which("piper")
    if not exe:
        try:
            import piper  # noqa: F401
            exe = "python3 -m piper"
        except Exception:
            return False
    models = Path(os.getenv("MODELS_DIR", "/models"))
    models.mkdir(parents=True, exist_ok=True)
    onnx = models / f"{PIPER_MODEL}.onnx"
    cfg = models / f"{PIPER_MODEL}.onnx.json"
    if not onnx.exists():
        log.info("piper: model download (once, ~60 MB)")
        subprocess.run(["python3", "-m", "piper.download_voices", "--data-dir", str(models), PIPER_MODEL],
                       capture_output=True, timeout=900)
    if not onnx.exists():
        return False
    cmd = (exe.split() if " " in exe else [exe]) + ["--model", str(onnx), "--config", str(cfg),
                                                    "--output_file", str(out.with_suffix(".wav"))]
    try:
        p = subprocess.run(cmd, input=text.encode(), capture_output=True, timeout=180)
        if p.returncode == 0 and out.with_suffix(".wav").exists():
            return True
        log.warning("piper failed: %s", p.stderr.decode()[:200])
    except Exception as e:
        log.warning("piper error: %s", e)
    return False


def edge_say(text: str, out: Path, hint: str) -> bool:
    try:
        import edge_tts                                        # pip install edge-tts
    except Exception:
        return False
    voices = json.loads(out.parent / "edge-voices.json") if (out.parent / "edge-voices.json").exists() else None
    if voices is None:
        try:
            async def _l():
                return [v["ShortName"] for v in await edge_tts.list_voices()]
            voices = asyncio.run(asyncio.wait_for(_l(), timeout=30))
            (out.parent / "edge-voices.json").write_text(json.dumps(voices))
        except Exception as e:
            log.warning("edge voices list failed: %s", e)
            voices = []
    want = {"ur": ("ur-PK", "ur-IN", "ur"), "en": ("en-GB", "en-US", "en")}[hint]
    pick = next((v for p in want for v in voices if v.startswith(p) and "Neural" in v), None)
    if not pick:
        return False

    async def _go():
        await edge_tts.Communicate(text, pick, rate="+4%").save(str(out))
    try:
        asyncio.run(asyncio.wait_for(_go(), timeout=90))
        return out.exists() and out.stat().st_size > 2000
    except Exception as e:
        log.warning("edge tts failed (%s): %s", pick, e)
        return False


def gtts_say(text: str, out: Path, lang: str) -> bool:
    try:
        from gtts import gTTS
    except Exception:
        return False
    for lg in dict.fromkeys([lang, "hi" if lang == "ur" else "en"]):
        try:
            gTTS(text, lang=lg, tld="co.in" if lg in ("ur", "hi") else "com").write(out)
            if out.exists() and out.stat().st_size > 1500:
                log.info("gtts ok lang=%s", lg)
                return True
        except Exception as e:
            log.warning("gtts %s failed: %s", lg, e)
    return False


def to_opus_wav(wav: Path, out: Path) -> bool:
    """piper gives .wav; WhatsApp voice notes are ogg/opus. ffmpeg is in the image."""
    if not shutil.which("ffmpeg"):
        out = wav
        return True
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libopus",
                        "-b:a", "24k", "-ac", "1", "-ar", "48000", str(out)], check=True, timeout=120)
        return out.exists() and out.stat().st_size > 500
    except Exception as e:
        log.warning("ffmpeg -> opus failed: %s", e)
        return False


# -------------------------------------------------------------------- api


@app.get("/healthz")
async def healthz():
    have = {"piper": bool(shutil.which("piper")) or _piper_importable(),
            "edge": _importable("edge_tts"), "gtts": _importable("gtts"),
            "ffmpeg": bool(shutil.which("ffmpeg"))}
    return {"ok": True, "backend_pref": BACKEND, "available": have, "public_url": PUBLIC,
            "model": PIPER_MODEL, "files": len(list(MEDIA.glob('*.ogg'))), "max_chars": MAX_CHARS}


def _importable(m: str) -> bool:
    try:
        __import__(m)
        return True
    except Exception:
        return False


def _piper_importable() -> bool:
    return _importable("piper")


@app.post("/tts")
async def tts(payload: dict | None = None):
    payload = payload or {}
    text = re.sub(r"\s+", " ", str(payload.get("text") or "")).strip()
    if not text:
        raise HTTPException(400, "text required")
    if len(text) > MAX_CHARS:
        raise HTTPException(413, f"too long for a voice note ({len(text)}>{MAX_CHARS}); keep it under "
                                f"~110 seconds of speech")
    text = text.replace("*", "").replace("#", "")
    key = str(hash(text + str(payload.get("voice", ""))))
    if key in _cache and Path(_cache[key]["path"]).exists():
        return JSONResponse(_cache[key])
    hint, lang = voice_for(text)
    fname = f"vn-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    out = MEDIA / f"{fname}.ogg"
    wav = MEDIA / f"{fname}.wav"
    mp3 = MEDIA / f"{fname}.mp3"
    tried: list[str] = []
    order = [BACKEND] if BACKEND != "auto" else ["piper", "edge", "gtts"]
    used = ""
    for b in order:
        if b == "piper" and hint == "en" and piper_say(text, wav):
            if to_opus_wav(wav, out):
                used = "piper"
                break
            out = wav                                  # no ffmpeg -> send the wav
            used = "piper"
            break
        tried.append(b + ":n/a")
        if b == "edge" and edge_say(text, mp3, hint):
            used = "edge"
            out = mp3
            break
        if b == "gtts" and gtts_say(text, mp3, lang):
            used = "gtts"
            out = mp3
            break
    if not used or not out.exists():
        raise HTTPException(502, json.dumps({"error": "no TTS backend produced audio", "tried": tried,
                                            "hint": "pip install edge-tts  (or)  pip install piper-tts"}))
    try:
        wav.unlink()
    except Exception:
        pass
    res = {"ok": True, "backend": used, "voice_hint": hint, "seconds_guess": round(len(text) / 14),
           "bytes": out.stat().st_size, "path": str(out), "url": f"{PUBLIC}/media/{out.name}",
           "filename": out.name, "mimetype": "audio/ogg; codecs=opus" if out.suffix == ".ogg" else "audio/mpeg"}
    _cache[key] = res
    return JSONResponse(res)


@app.get("/voices")
async def voices():
    return {"piper_model": PIPER_MODEL, "hint_rules": {"urdu": "ur (edge) / ur (gtts)", "other": "en"},
            "detection": "looks_urdu() = Arabic script OR >=2 Roman-Urdu markers"}


@app.post("/say-outreach")
async def say_outreach(payload: dict | None = None):
    """Handy: give it the same fields as the text message, get a spoken version.
    Script first (so it is short and natural), then audio."""
    payload = payload or {}
    script = str(payload.get("script") or "").strip()
    if not script:
        raise HTTPException(400, "script required (keep it 25-45 words, spoken style)")
    if len(script) > MAX_CHARS:
        script = script[:MAX_CHARS]
    return await tts({"text": script, "voice": payload.get("voice")})
