"""Stage: moving shots.

Wan 2.2 needs a GPU, so this stage does not call a model directly. It:
  1. computes each shot's clip key from its keyframe + motion prompt + video settings,
  2. uses outputs/clips/<shot>-<key>.mp4 when it exists (generated earlier on Kaggle),
  3. otherwise plans a Ken Burns move over the keyframe (the documented fallback), and
  4. writes outputs/clips/job.json listing only the clips that are missing.

`python -m pipeline kaggle-push` sends that job to a Kaggle T4; `kaggle-pull` brings the
clips back. Changing one motion prompt therefore puts exactly one clip in the job.
"""
from __future__ import annotations

import json

from .common import ROOT, StageResult, file_hash, log_event, read_current, rel, require_consent, say, stable_hash, stage_dir, write_current

STAGE = "clips"
NEGATIVE_VIDEO = ("text, watermark, blurry, distorted face, extra limbs, deformed hands, flicker, morphing, "
                  "sudden camera movement, low quality, jpeg artifacts")


def video_settings(sb: dict) -> dict:
    v = sb["models"]["video"]
    size = v.get("draft_size", "480x480") if sb.get("draft", True) else v.get("final_size", "704x704")
    w, h = (int(x) for x in size.split("x"))
    return {"model": v["model"], "width": w, "height": h, "steps": v.get("steps", 30), "frames": v.get("frames", 121), "fps": 24}


def clip_key(keyframe_hash: str, shot: dict, settings: dict) -> str:
    return stable_hash({"keyframe": keyframe_hash, "motion": shot["motion_prompt"], "seed": shot["seed"], **settings})


def run(sb: dict) -> StageResult:
    keyframes = read_current("keyframes")
    settings = video_settings(sb)
    provider = sb["models"]["video"]["provider"]
    d = stage_dir(STAGE)
    res = StageResult()
    current, job = {}, []
    for shot in sb["shots"]:
        sid = shot["id"]
        kf = keyframes.get(sid)
        if not kf or not (ROOT / kf).exists():
            say(f"  {sid}: no keyframe yet -> assembly will use a black frame")
            current[sid] = {"type": "none"}
            res.failed += 1
            continue
        require_consent(sb, shot.get("characters", []), "likeness")
        key = clip_key(file_hash(ROOT / kf), shot, settings)
        clip = d / f"{sid}-{key}.mp4"
        if clip.exists():
            current[sid] = {"type": "clip", "file": rel(clip), "key": key, "keyframe": kf}
            log_event(STAGE, shot=sid, provider="kaggle", model=settings["model"], cache="hit", key=key, output=rel(clip))
            say(f"  {sid}: Wan clip cached")
            res.cached += 1
            continue
        current[sid] = {"type": "kenburns", "keyframe": kf, "key": key, **shot.get("kenburns", {})}
        if provider == "kaggle":
            job.append({"shot": sid, "key": key, "keyframe": kf, "keyframe_name": f"{sid}-{key}.png",
                        "prompt": shot["motion_prompt"], "negative_prompt": NEGATIVE_VIDEO, "seed": shot["seed"], **settings})
            say(f"  {sid}: no Wan clip for this version -> queued for Kaggle; Ken Burns fallback for now")
        else:
            say(f"  {sid}: Ken Burns (video provider = kenburns)")
        res.fallback += 1
    write_current(STAGE, current)
    (d / "job.json").write_text(json.dumps({"video": job}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log_event(STAGE, provider="planner", status="ok", queued=[j["shot"] for j in job], settings=settings)
    if job:
        say(f"  {len(job)} clip(s) queued in outputs/clips/job.json -> run: python -m pipeline kaggle-push")
    return res
