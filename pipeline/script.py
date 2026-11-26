"""Stage: script check (Gemini, text only; does not modify storyboard.json).

The storyboard is written and owned by a human. This stage checks it:
  * word and character counts per line and an estimated spoken length,
  * a blind back-translation of each Amharic line into English by Gemini, shown next
    to the subtitle we wrote, so meaning drift between narration and subtitles is
    caught before anything is voiced.
Results: outputs/script/check.json. Cached per line, so only edited lines are re-sent.
"""
from __future__ import annotations

import json
import time

from .common import MissingKey, ProviderError, log_event, say, stable_hash, stage_dir
from .providers import PRICES, gemini_text

STAGE = "script"
PROMPT = ("Translate this {lang} sentence into natural English. It is one line of narration from a short family film. "
          "Reply with the English translation only.\n\n{text}")


def run(sb: dict) -> dict:
    d = stage_dir(STAGE)
    cache_file = d / "cache.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    model = sb["models"].get("text", {}).get("model", "gemini-3.8-flash")
    rows, words, chars = [], 0, 0
    for s in sb["shots"]:
        line = s["narration"].strip()
        if not line:
            continue
        w = len(line.split())
        words += w
        chars += len(line)
        key = stable_hash({"model": model, "text": line, "lang": sb["language"]})
        back = cache.get(key)
        if back is None:
            t0 = time.time()
            try:
                back, meta = gemini_text(PROMPT.format(lang="Amharic" if sb["language"] == "am" else sb["language"], text=line), model)
                back = back.strip()
                cache[key] = back
                log_event(STAGE, shot=s["id"], provider="gemini", model=meta["model"], params={"task": "back-translate"},
                          latency_s=round(time.time() - t0, 2), cost_usd_list=PRICES["gemini_text_usd_per_call"], cost_usd_paid=0.0,
                          status="ok", usage=meta.get("usage"))
            except (ProviderError, MissingKey) as e:
                log_event(STAGE, shot=s["id"], provider="gemini", model=model, status="error", error=str(e)[:300])
                back = f"(back-translation unavailable: {e})"
        else:
            log_event(STAGE, shot=s["id"], provider="gemini", model=model, cache="hit")
        rows.append({"shot": s["id"], "words": w, "chars": len(line), "subtitle_en": s["subtitle_en"], "back_translation": back})
        say(f"  {s['id']}: {w:2d} words | ours: {s['subtitle_en']}\n        gemini: {back}")
    cache_file.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    report = {"lines": rows, "total_words": words, "total_chars": chars,
              "note": "Amharic words are long; measure real speech length from the narration stage, not words/sec."}
    (d / "check.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    say(f"  total: {words} words, {chars} characters")
    return report
