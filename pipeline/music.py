"""Stage: score.

ACE-Step 1.5 runs on Kaggle alongside Wan (see kaggle/gpu_jobs.py). It returns N
candidates for one caption; storyboard.models.music.chosen picks which one the film uses
(the reason for the choice is written in EXPERIMENTS.md).

Until candidates exist, a placeholder score is synthesised locally with FFmpeg: a soft
D-major pad. That is the temp track for the radio cut.
"""
from __future__ import annotations

import json

from .common import StageResult, ffmpeg, log_event, rel, say, stable_hash, stage_dir, write_current

STAGE = "music"


def music_key(sb: dict) -> str:
    m, cfg = sb["music"], sb["models"]["music"]
    return stable_hash({"caption": m["caption"], "bpm": m.get("bpm"), "key": m.get("key"), "duration": m["duration_s"],
                        "seed": m.get("seed", 0), "model": cfg["model"], "n": cfg.get("candidates", 3)})


def placeholder(duration: float) -> "Path":
    """Temp track: D-major pad (D3, F#3, A3, D4) with slow tremolo and echo. Deterministic."""
    out = stage_dir(STAGE) / f"placeholder-{stable_hash({'d': round(duration, 1), 'v': 2})}.wav"
    if out.exists():
        return out
    notes = [146.83, 185.00, 220.00, 293.66]
    inputs = []
    for f in notes:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={f}:sample_rate=48000:duration={duration}"]
    mix = "".join(f"[{i}]" for i in range(len(notes)))
    graph = (f"{mix}amix=inputs={len(notes)}:normalize=1,tremolo=f=0.25:d=0.35,"
             f"aecho=0.8:0.7:420|900:0.25|0.18,lowpass=f=1800,afade=t=in:d=3,afade=t=out:st={max(duration - 4, 0)}:d=4,"
             f"volume=0.6[a]")
    ffmpeg([*inputs, "-filter_complex", graph, "-map", "[a]", "-ac", "2", str(out)], stage=STAGE, what="placeholder score")
    return out


def run(sb: dict) -> StageResult:
    cfg = sb["models"]["music"]
    d = stage_dir(STAGE)
    key = music_key(sb)
    n = cfg.get("candidates", 3)
    cands = sorted(d.glob(f"cand*-{key}.wav")) + sorted(d.glob(f"cand*-{key}.flac"))
    res = StageResult()
    job = []
    if cands:
        idx = min(cfg.get("chosen", 0), len(cands) - 1)
        chosen = cands[idx]
        log_event(STAGE, provider="kaggle", model=cfg["model"], cache="hit", key=key, output=rel(chosen), candidates=len(cands))
        say(f"  score: {len(cands)} ACE-Step candidate(s) cached, using #{idx} -> {rel(chosen)}")
        current = {"type": "acestep", "file": rel(chosen), "key": key, "candidates": [rel(c) for c in cands], "chosen": idx}
        res.cached += 1
    else:
        ph = placeholder(sb["music"]["duration_s"])
        say(f"  score: no ACE-Step candidates for this caption yet -> placeholder pad {rel(ph)}")
        current = {"type": "placeholder", "file": rel(ph), "key": key}
        if cfg["provider"] == "kaggle":
            m = sb["music"]
            job.append({"key": key, "caption": m["caption"], "bpm": m.get("bpm"), "keyscale": m.get("key", ""),
                        "duration": m["duration_s"], "seed": m.get("seed", 0), "candidates": n, "model": cfg["model"]})
            say("  score: queued for Kaggle (kaggle-push)")
        res.fallback += 1
    write_current(STAGE, current)
    (d / "job.json").write_text(json.dumps({"music": job}, indent=2) + "\n", encoding="utf-8")
    return res
