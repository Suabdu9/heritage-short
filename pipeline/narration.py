"""Stage: narration. One audio file per shot, so a changed line re-voices only that shot.

Primary: Addis Assistant (native Amharic voices). Fallback: Gemini TTS.
Every file is converted to 48 kHz mono WAV and its duration measured, because the
narration length sets the shot length in assembly ("cut picture to the voice").
"""
from __future__ import annotations

import time
from pathlib import Path

from .common import (MissingKey, ProviderError, StageResult, cached, ffmpeg, log_event, probe_duration, rel,
                     require_consent, say, stable_hash, write_current)
from .providers import PRICES, addis_tts, gemini_tts

STAGE = "narration"


class NarrationNotApproved(RuntimeError):
    pass


def narration_key(sb: dict, shot: dict, provider: str, voice: str) -> str:
    tts = sb["models"]["tts"]
    return stable_hash({
        "text": shot["narration"].strip(),
        "provider": provider,
        "voice": voice,
        "language": sb["language"],
        "style": tts.get("style_prompt", "") if provider == "gemini" else "",
        "speed": tts.get("speed"),
    })


def _synth(sb: dict, shot: dict, provider: str, voice: str) -> tuple[bytes, dict]:
    tts = sb["models"]["tts"]
    if provider == "addis":
        settings = {"speed": tts["speed"]} if tts.get("speed") else None
        return addis_tts(shot["narration"].strip(), voice, sb["language"], settings)
    if provider == "gemini":
        return gemini_tts(shot["narration"].strip(), voice, tts.get("style_prompt", ""))
    raise ValueError(provider)


def run(sb: dict, only: set[str] | None = None, force_provider: str | None = None) -> StageResult:
    tts = sb["models"]["tts"]
    chain = [(force_provider or tts["provider"], tts["voice"] if not force_provider or force_provider == tts["provider"] else tts.get("fallback_voice", ""))]
    fb = tts.get("fallback_provider", "none")
    if not force_provider and fb != "none":
        chain.append((fb, tts.get("fallback_voice", "")))

    # The narration tells the real people's story (and quotes my father), so the
    # 'story' consent of every real character is required before any line is voiced.
    require_consent(sb, [c["id"] for c in sb["characters"]], "story")
    if not sb.get("narration_review", {}).get("approved"):
        raise NarrationNotApproved(
            "the narration text has not been approved yet. Read the lines (python -m pipeline review), fix them in "
            "storyboard.json, then set narration_review.approved = true with your name and the date.")
    res = StageResult()
    current: dict[str, dict] = {}
    for shot in sb["shots"]:
        if not shot["narration"].strip():
            current[shot["id"]] = {"file": None, "duration_s": 0.0}
            continue
        # `only` limits which shots may make NEW calls; other shots still resolve
        # their cached file so assembly sees the whole film.

        done = False
        errors = []
        for provider, voice in chain:
            key = narration_key(sb, shot, provider, voice)
            out = cached(STAGE, shot["id"], key, "wav")
            if out.exists():
                dur = probe_duration(out)
                log_event(STAGE, shot=shot["id"], provider=provider, model=voice, cache="hit", key=key, output=rel(out), duration_s=round(dur, 2))
                say(f"  {shot['id']}: cached ({provider}/{voice}, {dur:.1f}s)")
                current[shot["id"]] = {"file": rel(out), "duration_s": round(dur, 2), "provider": provider, "voice": voice}
                res.cached += 1
                done = True
                break
            if only and shot["id"] not in only:
                continue
            t0 = time.time()
            try:
                audio, meta = _synth(sb, shot, provider, voice)
            except (ProviderError, MissingKey) as e:
                dt = time.time() - t0
                log_event(STAGE, shot=shot["id"], provider=provider, model=voice, status="error", latency_s=round(dt, 2), error=str(e)[:400])
                errors.append(f"{provider}: {e}")
                say(f"  {shot['id']}: {provider} failed -> {e}")
                continue
            dt = time.time() - t0
            raw = out.with_suffix(".src." + meta.get("format", "mp3"))
            raw.write_bytes(audio)
            # Trim leading/trailing silence so timing is predictable, then 48 kHz mono.
            ffmpeg(["-i", str(raw), "-af",
                    "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,"
                    "areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.1,areverse",
                    "-ar", "48000", "-ac", "1", str(out)], stage=STAGE, what=f"normalise {shot['id']}")
            raw.unlink(missing_ok=True)
            dur = probe_duration(out)
            chars = len(shot["narration"].strip())
            if provider == "gemini":
                cost = chars / 1000 * PRICES["gemini_tts_usd_per_1k_chars"]
            else:
                credits = meta.get("credits_used")
                cost = (credits / PRICES["etb_per_usd"]) if isinstance(credits, (int, float)) else None
            log_event(STAGE, shot=shot["id"], provider=provider, model=meta.get("model", voice), params={"voice": voice, "chars": chars},
                      seed=None, latency_s=round(dt, 2), cost_usd_list=cost, cost_usd_paid=0.0 if provider == "gemini" else cost,
                      status="ok", cache="miss", key=key, output=rel(out), duration_s=round(dur, 2),
                      provider_meta={k: v for k, v in meta.items() if k not in ("format",)})
            say(f"  {shot['id']}: generated with {provider}/{voice} in {dt:.1f}s -> {dur:.1f}s of speech")
            current[shot["id"]] = {"file": rel(out), "duration_s": round(dur, 2), "provider": provider, "voice": voice}
            if provider != chain[0][0]:
                res.fallback += 1
            res.generated += 1
            done = True
            break
        if not done:
            if only and shot["id"] not in only:
                continue
            if errors and all("missing " in e and "_KEY" in e for e in errors):
                raise MissingKey("no TTS key available: " + "; ".join(errors))
            res.failed += 1
            say(f"  {shot['id']}: NO NARRATION ({'; '.join(errors)})")
    write_current(STAGE, current)
    return res


def total_speech(sb: dict) -> float:
    from .common import read_current
    cur = read_current(STAGE)
    return sum(v.get("duration_s", 0) for v in cur.values())


def review(sb: dict) -> None:
    """Print every narration line with its subtitle, for a native speaker to check."""
    from .common import say
    st = sb.get("narration_review", {})
    say(f"narration approved: {st.get('approved')}  by: {st.get('by') or '-'}  date: {st.get('date') or '-'}\n")
    for s in sb["shots"]:
        if s["narration"].strip():
            say(f"{s['id']}  {s['narration']}\n      EN: {s['subtitle_en']}\n")


def audition(sb: dict, voices: list[str] | None = None, text: str | None = None) -> list[str]:
    """Voice the same line with several Addis voices into outputs/voices/ so you can
    listen and choose. Every call is logged. Does not need narration approval."""
    from .common import ROOT, stage_dir
    from .providers import addis_voices
    d = stage_dir("voices")
    if not voices:
        try:
            listing = addis_voices(sb["language"])
            for v in listing:
                say(f"  {v.get('voice_id') or v.get('id'):<20} {v.get('gender', ''):<8} {v.get('descriptor') or v.get('description') or ''}")
            voices = [v.get("voice_id") or v.get("id") for v in listing][:6]
        except ProviderError as e:
            say(f"  could not list voices ({e}); using a default set")
            voices = ["am-yohanes-calm", "am-meron", "am-sara", "am-hamen", "am-tesfa", "am-makda"]
    line = text or next(s["narration"] for s in sb["shots"] if s["narration"].strip())
    outs = []
    for v in voices:
        t0 = time.time()
        try:
            audio, meta = addis_tts(line, v, sb["language"])
        except (ProviderError, MissingKey) as e:
            log_event("voices", provider="addis", model=v, status="error", error=str(e)[:300])
            say(f"  {v}: failed -> {e}")
            if isinstance(e, MissingKey):
                raise
            continue
        p = d / f"{v}.mp3"
        p.write_bytes(audio)
        log_event("voices", provider="addis", model=v, purpose="audition", latency_s=round(time.time() - t0, 2),
                  status="ok", output=rel(p), chars=len(line), credits_used=meta.get("credits_used"),
                  credits_remaining=meta.get("credits_remaining"))
        say(f"  {v}: {rel(p)}  ({time.time() - t0:.1f}s)")
        outs.append(rel(p))
    say("\nListen to each file, then set models.tts.voice in storyboard.json to the one you like.")
    return outs
