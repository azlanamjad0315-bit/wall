"""ai.py — provider-agnostic chat with free-tier survival logic.

Why this exists: free tiers (Gemini / Groq / Ollama) each have RPM *and* RPD caps,
they return 429 in bursts, and small models emit JSON wrapped in prose. Doing that in
n8n means 40 nodes of IF/switch. Here it is 150 lines of testable Python.

Guarantees:
  * walks a provider chain and falls over on 429 / 5xx / timeout / empty completion
  * keeps a per-provider daily request counter so we never blow past a free RPD cap
  * `chat_json` tolerates ``` fences, prose-wrapped JSON, trailing commas, smart quotes
    and does one auto-repair round-trip before giving up
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("gateway.ai")

# -------------------------------------------------------------------------- config


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    model_fast: str = ""
    style: str = "openai"          # openai | ollama
    extra: dict[str, Any] = field(default_factory=dict)
    min_interval_ms: int = 0       # pace helper for very tight RPM limits

    @property
    def usable(self) -> bool:
        return bool(self.base_url) and (bool(self.api_key) or self.style == "ollama")


def _env(k: str, d: str = "") -> str:
    return (os.getenv(k) or d).strip()


def build_chain() -> list[Provider]:
    """Chain order comes from AI_PROVIDER_CHAIN (default: gemini,groq,ollama)."""
    reg: dict[str, Provider] = {}

    gkey = _env("GEMINI_API_KEY")
    if gkey:
        extra = {}
        if _env("GEMINI_DISABLE_THINKING", "true").lower() in ("1", "true", "yes"):
            # burns input tokens for nothing on a 250k TPM free tier
            extra["thinking_config"] = {"thinking_budget": 0, "include_thoughts": False}
        reg["gemini"] = Provider(
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            gkey,
            _env("GEMINI_MODEL", "gemini-2.5-flash"),
            _env("GEMINI_MODEL_FAST", "gemini-2.5-flash-lite"),
            extra=extra,
            min_interval_ms=int(_env("GEMINI_MIN_INTERVAL_MS", "4200")),  # ~14 RPM
        )

    qkey = _env("GROQ_API_KEY")
    if qkey:
        reg["groq"] = Provider(
            "groq",
            "https://api.groq.com/openai/v1",
            qkey,
            _env("GROQ_MODEL", "gpt-oss-120b"),
            _env("GROQ_MODEL_FAST", "gpt-oss-20b"),
            min_interval_ms=int(_env("GROQ_MIN_INTERVAL_MS", "2100")),
        )

    ourl = _env("OLLAMA_BASE_URL", "http://ollama:11434")
    reg["ollama"] = Provider(
        "ollama", ourl.rstrip("/"), "ollama", _env("OLLAMA_MODEL", "qwen2.5:14b-instruct"),
        _env("OLLAMA_MODEL_FAST", _env("OLLAMA_MODEL", "qwen2.5:14b-instruct")),
        style="ollama",
    )

    # OpenAI-compatible extras (OpenRouter free models, Mistral dev tier, ...)
    for raw in _env("AI_EXTRA_PROVIDERS", "").split(";"):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split("|")
        if len(parts) >= 4:
            reg[parts[0]] = Provider(parts[0], parts[1].rstrip("/"), parts[2], parts[3],
                                      parts[4] if len(parts) > 4 else parts[3])

    names = [n.strip() for n in _env("AI_PROVIDER_CHAIN", "gemini,groq,ollama").split(",") if n.strip()]
    chain = [reg[n] for n in names if n in reg and reg[n].usable]
    if not chain:
        log.warning("no usable AI provider (set GEMINI_API_KEY or GROQ_API_KEY or run Ollama)")
    return chain


class AiError(RuntimeError):
    def __init__(self, msg: str, *, status: int = 0, attempts: list[str] | None = None):
        super().__init__(msg)
        self.status = status
        self.attempts = attempts or []


# ------------------------------------------------------------------- daily budget


class Budget:
    """Per-provider daily request counter, persisted so container restarts don't reset it."""

    def __init__(self, data_dir: str | None = None, cap: int | None = None):
        self.cap = cap if cap is not None else int(_env("AI_DAILY_REQUESTS_PER_PROVIDER", "400") or 400)
        self.dir = Path(data_dir or _env("DATA_DIR", "/data"))

    def _file(self) -> Path:
        return self.dir / f"usage-{date.today().isoformat()}.json"

    def _read(self) -> dict[str, int]:
        try:
            return json.loads(self._file().read_text())
        except Exception:
            return {}

    def used(self, provider: str) -> int:
        return self._read().get(provider, 0)

    def left(self, provider: str) -> int:
        if self.cap <= 0:
            return 10**9
        return max(0, self.cap - self.used(provider))

    def burn(self, provider: str) -> None:
        d = self._read()
        d[provider] = d.get(provider, 0) + 1
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._file().write_text(json.dumps(d))
        except Exception as e:                                     # non fatal
            log.debug("usage persist failed: %s", e)


# ------------------------------------------------------------------------- client


class AiClient:
    def __init__(self, chain: list[Provider] | None = None, budget: Budget | None = None,
                 transport: Any = None):
        self.chain = chain if chain is not None else build_chain()
        self.transport = transport          # tests inject httpx.MockTransport
        self.budget = budget or Budget()
        self._last_call: dict[str, float] = {}
        self.timeout = float(_env("AI_TIMEOUT_SECONDS", "240"))
        self.max_tokens = int(_env("AI_MAX_TOKENS", "16384"))
        self.temperature = float(_env("AI_TEMPERATURE", "0.85"))

    # -- plumbing ---------------------------------------------------------
    @staticmethod
    def _headers(p: Provider) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if p.style == "ollama":
            return h
        h["Authorization"] = f"Bearer {p.api_key}"
        return h

    def _payload(self, p: Provider, messages: list[dict], max_tokens: int,
                 temperature: float, json_mode: bool, fast: bool) -> dict:
        model = (p.model_fast if fast else p.model) or p.model
        if p.style == "ollama":
            pl: dict[str, Any] = {
                "model": model, "messages": messages, "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            if json_mode:
                pl["format"] = "json"
            return pl
        pl = {"model": model, "messages": messages, "temperature": temperature,
              "max_tokens": max_tokens}
        if json_mode:
            pl["response_format"] = {"type": "json_object"}
        pl.update(p.extra)
        return pl

    def _url(self, p: Provider) -> str:
        if p.style == "ollama":
            return p.base_url + "/api/chat"
        return p.base_url.rstrip("/") + "/chat/completions"

    @staticmethod
    def _text_of(p: Provider, data: dict) -> str:
        try:
            if p.style == "ollama":
                return (data.get("message") or {}).get("content") or ""
            ch = (data.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            out = msg.get("content")
            if not out and isinstance(msg.get("reasoning_content"), str):
                out = msg["reasoning_content"]          # some models put it there
            if not out and isinstance(ch.get("text"), str):
                out = ch["text"]
            return out or ""
        except Exception:
            return ""

    async def _throttle(self, p: Provider) -> None:
        if p.min_interval_ms <= 0:
            return
        wait = p.min_interval_ms / 1000.0 - (time.monotonic() - self._last_call.get(p.name, 0))
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call[p.name] = time.monotonic()

    # -- public -----------------------------------------------------------
    async def chat(self, messages: list[dict], *, max_tokens: int | None = None,
                   temperature: float | None = None, json_mode: bool = False,
                   fast: bool = False, tag: str = "") -> dict:
        """Returns {text, provider, model, attempts, usage}. Raises AiError."""
        if not self.chain:
            raise AiError("no AI provider configured — set GEMINI_API_KEY or GROQ_API_KEY in .env")
        mt = max_tokens or self.max_tokens
        tp = self.temperature if temperature is None else temperature
        attempts: list[str] = []
        last_status = 0

        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as cx:
            for p in self.chain:
                if self.budget.left(p.name) <= 0:
                    attempts.append(f"{p.name}:daily-cap")
                    continue
                await self._throttle(p)
                for tries in range(2):                    # 1 retry for transient 429/5xx
                    self.budget.burn(p.name)
                    try:
                        r = await cx.post(self._url(p), headers=self._headers(p),
                                          json=self._payload(p, messages, mt, tp, json_mode, fast))
                    except (httpx.TimeoutException, httpx.TransportError) as e:
                        attempts.append(f"{p.name}:net:{type(e).__name__}")
                        last_status = -1
                        break
                    if r.status_code == 200:
                        try:
                            data = r.json()
                        except Exception:
                            attempts.append(f"{p.name}:bad-json-response")
                            break
                        text = self._text_of(p, data).strip()
                        if text:
                            return {"text": text, "provider": p.name,
                                    "model": (p.model_fast if fast else p.model) or p.model,
                                    "attempts": attempts, "tag": tag,
                                    "usage": data.get("usage") or {},
                                    "quota_left": self.budget.left(p.name)}
                        attempts.append(f"{p.name}:empty")
                        break
                    last_status = r.status_code
                    attempts.append(f"{p.name}:{r.status_code}")
                    if r.status_code in (408, 409, 429, 500, 502, 503, 504, 529):
                        ra = r.headers.get("retry-after")
                        delay = min(60.0, float(ra)) if ra and ra.replace(".", "", 1).isdigit() else 6.0 * (tries + 1)
                        if tries == 0:
                            log.warning("%s -> %s, backing off %.0fs (%s)", p.name, r.status_code, delay,
                                        (r.text or "")[:180].replace("\n", " "))
                            await asyncio.sleep(delay)
                            continue
                    else:
                        log.warning("%s rejected (%s): %s", p.name, r.status_code,
                                    (r.text or "")[:240].replace("\n", " "))
                    break                                    # next provider
        raise AiError(
            f"all AI providers failed for task '{tag or 'chat'}' [{', '.join(attempts) or 'none tried'}]",
            status=last_status, attempts=attempts)

    async def chat_json(self, messages: list[dict], *, keys_hint: list[str] | None = None,
                        max_tokens: int | None = None, temperature: float = 0.3,
                        fast: bool = False, tag: str = "") -> tuple[dict, dict]:
        """Returns (parsed_object, meta). One repair pass on parse failure."""
        meta = await self.chat(messages, json_mode=True, max_tokens=max_tokens,
                               temperature=temperature, fast=fast, tag=tag)
        obj = extract_json(meta["text"])
        if obj is None and keys_hint:
            obj = coerce_keys(meta["text"], keys_hint)
        if obj is None:
            log.info("%s: JSON parse failed, asking model to repair", tag or "chat_json")
            repair = messages + [
                {"role": "assistant", "content": meta["text"][:6000]},
                {"role": "user", "content": "Your answer above was not valid JSON. Repeat it as ONE "
                                            "valid JSON object only: no markdown, no ``` fences, no text "
                                            "before or after, double-quoted keys, escape newlines in strings."},
            ]
            meta2 = await self.chat(repair, json_mode=True, temperature=0.0, fast=True, tag=tag + ":repair")
            obj = extract_json(meta2["text"])
            meta2["text"] = meta2["text"]
            meta = meta2
        if obj is None:
            raise AiError(f"{tag or 'chat_json'}: model never returned valid JSON "
                          f"(sample: {meta['text'][:200]!r})")
        return obj, meta


# --------------------------------------------------------------- JSON extraction

_FENCE = re.compile(r"```(?:json|javascript|json5)?\s*(.+?)```", re.S | re.I)


def _balanced(s: str, start: int) -> str | None:
    depth, in_str, esc = 0, False, False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


def extract_json(text: str) -> dict | None:
    """Best-effort JSON extraction from an LLM completion."""
    if not text:
        return None
    cands: list[str] = []
    for m in _FENCE.finditer(text):
        cands.append(m.group(1))
    for i, ch in enumerate(text):
        if ch == "{":
            blk = _balanced(text, i)
            if blk:
                cands.append(blk)
                break
    cands.insert(0, text.strip())
    for c in cands:
        for attempt in (c, _repair(c)):
            try:
                v = json.loads(attempt)
            except Exception:
                continue
            if isinstance(v, dict):
                return v
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return v[0]
    return None


_TRAILING = re.compile(r",(\s*[}\]])")
_SMART = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "\n": " "})


def _repair(s: str) -> str:
    s = s.strip()
    s = _TRAILING.sub(r"\1", s)
    s = re.sub(r"^\ufeff", "", s)
    s = s.replace("“", '"').replace("”", '"')
    # literal newlines inside strings break json.loads → escape them
    out, in_str, esc = [], False, False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            elif ch == "\n":
                out.append("\\n")
                continue
            elif ch == "\t":
                out.append("\\t")
                continue
        elif ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


def coerce_keys(text: str, keys_hint: list[str]) -> dict | None:
    """Fallback for weak models that ignore the schema: pull known keys out line by line."""
    low = {}
    for line in text.splitlines():
        m = re.match(r"^\s*[\"'`]?([A-Za-z0-9_]+)[\"'`]?\s*[:=]\s*(.*)$", line)
        if m:
            v = m.group(2).strip().strip(",").strip("\"'`")
            if v.lower() in ("true", "false"):
                v = v.lower() == "true"
            low[m.group(1).lower()] = v
    if not low:
        return None
    out: dict[str, Any] = {}
    for k in keys_hint:
        if k.lower() in low:
            out[k] = low[k.lower()]
    return out or None
