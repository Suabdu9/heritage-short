"""Send the GPU job to Kaggle and bring results back, using the official `kaggle` CLI.

push:   builds kaggle/build/dataset (job.json + only the keyframes that are queued),
        creates/versions the private dataset <user>/heritage-short-job, then pushes
        kaggle/gpu_jobs.py as a private GPU script kernel <user>/heritage-short-gpu.
status: prints the kernel status.
pull:   downloads the kernel output, moves clips into outputs/clips and score
        candidates into outputs/music, and copies each GPU result line into runs.jsonl
        (GPU seconds -> COSTS.md).

Needs KAGGLE_API_TOKEN (new KGAT_ tokens) or KAGGLE_USERNAME + KAGGLE_KEY in .env.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

from .common import OUT, ROOT, log_event, need_env, say
from .providers import PRICES

BUILD = ROOT / "kaggle" / "build"
DATASET_SLUG = "heritage-short-job"
KERNEL_SLUG = "heritage-short-gpu"


def _user() -> str:
    """Kaggle handle. New-style tokens (KGAT_..., env KAGGLE_API_TOKEN) need no username:
    ask the CLI. Old style: KAGGLE_USERNAME + KAGGLE_KEY. The handle has no spaces."""
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return os.environ["KAGGLE_USERNAME"].strip()
    if not os.environ.get("KAGGLE_API_TOKEN"):
        need_env("KAGGLE_API_TOKEN")
    for line in _kaggle("config", "view").splitlines():
        if "username:" in line:
            return line.split("username:", 1)[1].strip()
    raise RuntimeError("could not read the Kaggle username from `kaggle config view`")


def _kaggle(*args: str, check: bool = True) -> str:
    exe = shutil.which("kaggle")
    if not exe:
        raise RuntimeError("kaggle CLI not found: pip install kaggle")
    p = subprocess.run([exe, *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"kaggle {' '.join(args)} failed:\n{p.stdout}\n{p.stderr}")
    return p.stdout + p.stderr


def push() -> None:
    user = _user()
    video = json.loads((OUT / "clips" / "job.json").read_text())["video"] if (OUT / "clips" / "job.json").exists() else []
    music = json.loads((OUT / "music" / "job.json").read_text())["music"] if (OUT / "music" / "job.json").exists() else []
    if not video and not music:
        say("nothing queued: every clip and the score are already cached for this storyboard")
        return
    ds = BUILD / "dataset"
    shutil.rmtree(ds, ignore_errors=True)
    ds.mkdir(parents=True)
    for j in video:
        shutil.copy(ROOT / j["keyframe"], ds / j["keyframe_name"])
    (ds / "job.json").write_text(json.dumps({"video": video, "music": music}, indent=2, ensure_ascii=False))
    (ds / "dataset-metadata.json").write_text(json.dumps({"title": DATASET_SLUG, "id": f"{user}/{DATASET_SLUG}",
                                                          "licenses": [{"name": "CC0-1.0"}]}, indent=2))
    st = _kaggle("datasets", "status", f"{user}/{DATASET_SLUG}", check=False).lower()
    exists = any(w in st for w in ("ready", "pending", "complete"))
    if exists:
        say(_kaggle("datasets", "version", "-p", str(ds), "-m", "new job", "--dir-mode", "zip"))
    else:
        say(_kaggle("datasets", "create", "-p", str(ds), "--dir-mode", "zip"))

    kd = BUILD / "kernel"
    shutil.rmtree(kd, ignore_errors=True)
    kd.mkdir(parents=True)
    shutil.copy(ROOT / "kaggle" / "gpu_jobs.py", kd / "gpu_jobs.py")
    (kd / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{user}/{KERNEL_SLUG}", "title": KERNEL_SLUG, "code_file": "gpu_jobs.py", "language": "python",
        "kernel_type": "script", "is_private": True, "enable_gpu": True, "enable_internet": True,
        "machine_shape": "NvidiaTeslaT4", "dataset_sources": [f"{user}/{DATASET_SLUG}"], "competition_sources": [],
        "kernel_sources": []}, indent=2))
    say(_kaggle("kernels", "push", "-p", str(kd)))
    log_event("kaggle", provider="kaggle", action="push", queued_video=[j["shot"] for j in video], queued_music=bool(music))
    say(f"pushed. Watch it: python -m pipeline kaggle-status  (or https://www.kaggle.com/code/{user}/{KERNEL_SLUG})")


def status() -> None:
    user = _user()
    say(_kaggle("kernels", "status", f"{user}/{KERNEL_SLUG}"))


def pull() -> None:
    user = _user()
    inbox = BUILD / "incoming"
    shutil.rmtree(inbox, ignore_errors=True)
    inbox.mkdir(parents=True)
    say(_kaggle("kernels", "output", f"{user}/{KERNEL_SLUG}", "-p", str(inbox)))
    moved = 0
    for f in inbox.rglob("*.mp4"):
        shutil.move(str(f), OUT / "clips" / f.name)
        moved += 1
    (OUT / "music").mkdir(parents=True, exist_ok=True)
    for f in inbox.rglob("cand*.wav"):
        shutil.move(str(f), OUT / "music" / f.name)
        moved += 1
    gpu_s = 0.0
    for res in inbox.rglob("results.jsonl"):
        for line in res.read_text().splitlines():
            r = json.loads(line)
            secs = float(r.get("seconds", 0) or 0)
            gpu_s += secs
            log_event("clips" if r.get("kind") == "video" else "music", provider="kaggle", model=r.get("model"),
                      shot=r.get("shot"), seed=r.get("seed"), status=r.get("status"), latency_s=secs, gpu_seconds=secs,
                      gpu=r.get("gpu"), setup_seconds=r.get("setup_seconds"),
                      cost_usd_list=round(secs / 3600 * PRICES["gpu_t4_usd_per_hour"], 4), cost_usd_paid=0.0,
                      params={k: r.get(k) for k in ("width", "height", "frames", "steps", "dtype", "thinking") if k in r},
                      output=r.get("file"), error=r.get("error"))
    say(f"pulled {moved} file(s); {gpu_s / 60:.1f} GPU minutes logged to runs.jsonl. Re-run the pipeline to use them.")
