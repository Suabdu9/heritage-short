"""Smoke test: one cheap call per provider, latency logged to runs.jsonl.
Run first, to learn early which keys and quotas work."""
from __future__ import annotations

import shutil
import subprocess
import time

from .common import MissingKey, ProviderError, log_event, say, stage_dir
from . import providers


def _check(name: str, fn) -> bool:
    t0 = time.time()
    try:
        detail = fn()
        dt = time.time() - t0
        log_event("smoke", provider=name, status="ok", latency_s=round(dt, 2), detail=detail)
        say(f"  OK    {name:<12} {dt:5.1f}s  {detail}")
        return True
    except (ProviderError, MissingKey, RuntimeError, OSError) as e:
        dt = time.time() - t0
        log_event("smoke", provider=name, status="error", latency_s=round(dt, 2), error=str(e)[:400])
        say(f"  FAIL  {name:<12} {dt:5.1f}s  {e}")
        return False


def run(sb: dict) -> bool:
    d = stage_dir("smoke")

    def gemini():
        names = providers.gemini_list_models()
        tts = [n for n in names if "tts" in n]
        text, meta = providers.gemini_text("Reply with the single word: ready", sb["models"].get("text", {}).get("model", "gemini-3.8-flash"), timeout=30, attempts=1)
        return f"text model {meta['model']} said '{text.strip()[:20]}'; tts models: {', '.join(tts[:4]) or 'none'}"

    def cloudflare():
        m = sb["models"]["image"]
        img, meta = providers.cloudflare_image(m["model"], "a small clay coffee pot on a wooden table, gouache",
                                               seed=1, steps=4, width=512, height=512)
        (d / "cloudflare.png").write_bytes(img)
        return f"{meta['model']} -> {len(img) // 1024} KB image"

    def addis():
        voices = providers.addis_voices(sb["language"])
        ids = [v.get("voice_id") or v.get("id") for v in voices]
        return f"{len(ids)} {sb['language']} voices: {', '.join(map(str, ids[:6]))}"

    def kaggle():
        exe = shutil.which("kaggle")
        if not exe:
            raise RuntimeError("kaggle CLI not installed (pip install kaggle)")
        p = subprocess.run([exe, "kernels", "list", "--mine", "--page-size", "3"], capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            raise RuntimeError((p.stdout + p.stderr).strip()[-300:] or f"exit code {p.returncode}")
        return "authenticated"

    def ffmpeg():
        from .common import FFMPEG, need_ffmpeg
        need_ffmpeg()
        return subprocess.run([FFMPEG(), "-version"], capture_output=True, text=True).stdout.split("\n")[0]

    say("smoke test (one call per provider):")
    results = [_check(n, f) for n, f in [("ffmpeg", ffmpeg), ("gemini", gemini), ("cloudflare", cloudflare),
                                         ("addis", addis), ("kaggle", kaggle)]]
    return all(results)
