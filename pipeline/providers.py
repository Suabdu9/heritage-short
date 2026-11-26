"""Thin HTTP clients for the hosted providers. Each returns raw output plus a small
`meta` dict (model actually used, usage numbers) that the calling stage logs.

Keys come from the environment (.env); they are sent only in headers and never logged.
"""
from __future__ import annotations

import base64
import io
import os
import time
import wave
from typing import Any

import requests

from .common import ProviderError, check_response, need_env, with_retries

TIMEOUT = 180
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
CF_BASE = "https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"
ADDIS_BASE = os.environ.get("ADDIS_BASE_URL", "https://api.addisassistant.com")

# List-price assumptions used for the "what would this cost on a paid plan" column.
# They are estimates; COSTS.md says where each number came from and when it was checked.
PRICES = {
    "cloudflare_image_usd": float(os.environ.get("PRICE_CF_IMAGE_USD", "0.0011")),   # ~1 schnell/klein 1024px image
    "gemini_tts_usd_per_1k_chars": float(os.environ.get("PRICE_GEMINI_TTS_1K", "0.01")),
    "gemini_text_usd_per_call": float(os.environ.get("PRICE_GEMINI_TEXT_CALL", "0.0005")),
    "addis_tts_etb_per_1k_chars": float(os.environ.get("PRICE_ADDIS_1K_ETB", "0")),  # read from API usage when returned
    "gpu_t4_usd_per_hour": float(os.environ.get("PRICE_T4_HOUR", "0.35")),           # GCP on-demand T4
    "etb_per_usd": float(os.environ.get("ETB_PER_USD", "155")),
}


# ----------------------------------------------------------------------- Gemini
_model_cache: dict[str, str] = {}


def _gemini_headers() -> dict:
    key = need_env("GEMINI_API_KEY")["GEMINI_API_KEY"]
    return {"x-goog-api-key": key, "Content-Type": "application/json"}


def gemini_list_models() -> list[str]:
    r = requests.get(f"{GEMINI_BASE}/models?pageSize=200", headers=_gemini_headers(), timeout=60)
    check_response(r, "gemini")
    return [m["name"].split("/", 1)[1] for m in r.json().get("models", [])]


def resolve_gemini_model(preferred: str, must_contain: str) -> str:
    """Model names move fast. If the preferred one is gone, pick the newest listed model
    whose name contains `must_contain` (e.g. 'tts' or 'flash') and say so in the log."""
    if preferred in _model_cache:
        return _model_cache[preferred]
    names = gemini_list_models()
    if preferred in names:
        _model_cache[preferred] = preferred
        return preferred
    candidates = sorted((n for n in names if must_contain in n), reverse=True)
    if must_contain != "tts":
        candidates = [n for n in candidates if "tts" not in n and "image" not in n and "embedding" not in n] or candidates
    if not candidates:
        raise ProviderError(f"no Gemini model matching '{must_contain}' is available to this key")
    _model_cache[preferred] = candidates[0]
    return candidates[0]


def gemini_text(prompt: str, model: str, *, temperature: float = 0.4, json_mode: bool = False, timeout: int = 60,
                attempts: int = 3) -> tuple[str, dict]:
    used = resolve_gemini_model(model, "flash")
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"

    def call():
        r = requests.post(f"{GEMINI_BASE}/models/{used}:generateContent", headers=_gemini_headers(), json=body, timeout=timeout)
        check_response(r, "gemini")
        return r.json()

    data = with_retries(call, what="gemini text", attempts=attempts)
    try:
        text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError) as e:
        raise ProviderError(f"gemini returned no text: {str(data)[:300]}") from e
    return text, {"model": used, "usage": data.get("usageMetadata", {})}


def gemini_tts(text: str, voice: str, style_prompt: str = "", model: str = "gemini-3.8-flash-tts") -> tuple[bytes, dict]:
    """Returns WAV bytes (Gemini sends raw 24 kHz 16-bit mono PCM; we wrap it)."""
    used = resolve_gemini_model(os.environ.get("GEMINI_TTS_MODEL", model), "tts")
    spoken = f"{style_prompt}\n\n{text}" if style_prompt else text
    body = {
        "contents": [{"parts": [{"text": spoken}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }

    def call():
        r = requests.post(f"{GEMINI_BASE}/models/{used}:generateContent", headers=_gemini_headers(), json=body, timeout=TIMEOUT)
        check_response(r, "gemini-tts")
        return r.json()

    data = with_retries(call, what="gemini tts")
    try:
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
    except (KeyError, IndexError) as e:
        raise ProviderError(f"gemini tts returned no audio: {str(data)[:300]}") from e
    pcm = base64.b64decode(part["data"])
    rate = 24000
    mime = part.get("mimeType", "")
    if "rate=" in mime:
        rate = int(mime.split("rate=")[1].split(";")[0])
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue(), {"model": used, "usage": data.get("usageMetadata", {}), "format": "wav"}


# ------------------------------------------------------------------- Cloudflare
def cloudflare_image(model: str, prompt: str, *, seed: int, steps: int, width: int, height: int,
                     reference_images: list[bytes] | None = None) -> tuple[bytes, dict]:
    """FLUX on Workers AI. FLUX.2 models take multipart form data and accept up to four
    reference images (used here for character consistency); FLUX.1 schnell takes JSON,
    text only, max 8 steps."""
    env = need_env("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
    url = CF_BASE.format(acct=env["CLOUDFLARE_ACCOUNT_ID"], model=model)
    headers = {"Authorization": f"Bearer {env['CLOUDFLARE_API_TOKEN']}"}
    is_flux2 = "flux-2" in model
    refs = (reference_images or [])[:4] if is_flux2 else []

    def call():
        if is_flux2:
            fields: dict[str, tuple] = {
                "prompt": (None, prompt),
                "seed": (None, str(seed)),
                "steps": (None, str(steps)),
                "width": (None, str(width)),
                "height": (None, str(height)),
            }
            for i, img in enumerate(refs):
                fields[f"input_image_{i}"] = (f"ref{i}.png", img, "image/png")
            r = requests.post(url, headers=headers, files=fields, timeout=TIMEOUT)
        else:
            r = requests.post(url, headers=headers, json={"prompt": prompt, "steps": min(steps, 8)}, timeout=TIMEOUT)  # schnell rejects "seed"
        check_response(r, f"cloudflare {model}")
        return r

    r = with_retries(call, what=f"cloudflare {model}")
    if r.headers.get("content-type", "").startswith("image/"):
        img = r.content
    else:
        data = r.json()
        if not data.get("success", True):
            raise ProviderError(f"cloudflare error: {data.get('errors')}")
        b64 = (data.get("result") or {}).get("image")
        if not b64:
            raise ProviderError(f"cloudflare returned no image: {str(data)[:300]}")
        img = base64.b64decode(b64)
    return img, {"model": model, "reference_images": len(refs)}


# ------------------------------------------------------------- Addis Assistant
def _addis_headers() -> dict:
    key = need_env("ADDIS_API_KEY")["ADDIS_API_KEY"]
    return {"x-api-key": key, "Content-Type": "application/json"}


def addis_voices(language: str = "am") -> list[dict]:
    r = requests.get(f"{ADDIS_BASE}/api/v1/voice/voices", headers=_addis_headers(), params={"language": language}, timeout=60)
    check_response(r, "addis")
    data = r.json()
    return data.get("data", data) if isinstance(data, dict) else data


def addis_tts(text: str, voice_id: str, language: str = "am", voice_settings: dict | None = None) -> tuple[bytes, dict]:
    """POST /api/v1/voice/generations (contract read from the official `addisai` SDK,
    v0.14.0). Returns a clip with a signed audio_url; we download the MP3."""
    body = {
        "text": text,
        "language": language,
        "voice_id": voice_id,
        "output_format": "mp3_44100",
        "voice_settings": voice_settings,
        "stream": False,
        "client_request_id": f"hs-{int(time.time() * 1000)}",
    }

    def call():
        r = requests.post(f"{ADDIS_BASE}/api/v1/voice/generations", headers=_addis_headers(), json=body, timeout=TIMEOUT)
        check_response(r, "addis")
        return r.json()

    data = with_retries(call, what="addis tts")
    clip = data.get("data", data)
    url = clip.get("audio_url") or (clip.get("playback") or {}).get("url")
    if not url:
        raise ProviderError(f"addis returned no audio_url: {str(clip)[:300]}")
    a = with_retries(lambda: _get_audio(url), what="addis download")
    usage = clip.get("usage") or {}
    return a, {
        "model": f"addis/{voice_id}",
        "clip_id": clip.get("id"),
        "duration_s": clip.get("duration_seconds"),
        "billable_characters": clip.get("billable_characters"),
        "credits_used": usage.get("credits_used"),
        "credits_remaining": usage.get("credits_remaining"),
        "currency": usage.get("currency", "ETB"),
        "format": "mp3",
    }


def _get_audio(url: str) -> bytes:
    r = requests.get(url, timeout=TIMEOUT)
    check_response(r, "addis audio download")
    return r.content
