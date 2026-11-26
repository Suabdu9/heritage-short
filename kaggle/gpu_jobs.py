"""Runs on a Kaggle GPU notebook (T4). Pushed by `python -m pipeline kaggle-push`.

Reads job.json from the attached dataset (built locally from the storyboard: only the
clips and score that are missing for the current storyboard), then:
  1. music: ACE-Step 1.5 turbo, N instrumental candidates (separate process, so its
     memory is released before Wan loads)
  2. video: Wan 2.2 TI2V-5B image-to-video, one clip per queued shot

Every output is written to /kaggle/working/out/ as soon as it is done, with one JSON line
per job in out/results.jsonl (GPU name, seconds, settings). A timed-out session still
leaves the finished clips behind. `kaggle-pull` downloads them into outputs/.
"""
import glob
import json
import os
import subprocess
import sys
import time

OUT = "/kaggle/working/out"
os.makedirs(OUT, exist_ok=True)


def find_job_dir():
    hits = glob.glob("/kaggle/input/**/job.json", recursive=True)
    if not hits:
        raise SystemExit("job.json not found under /kaggle/input; is the heritage-short-job dataset attached?")
    return os.path.dirname(hits[0])


def gpu_name():
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unknown"


def log(rec):
    rec.update({"gpu": gpu_name(), "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
    with open(f"{OUT}/results.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def pip(*pkgs):
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *pkgs], check=True)


# ------------------------------------------------------------------------ music
def run_music(job_dir):
    job = json.load(open(f"{job_dir}/job.json")).get("music", [])
    if not job:
        print("no music job")
        return
    m = job[0]
    t_setup = time.time()
    if not os.path.exists("/kaggle/working/ace"):
        subprocess.run(["git", "clone", "--depth", "1", "https://github.com/ace-step/ACE-Step-1.5.git", "/kaggle/working/ace"], check=True)
        pip("-e", "/kaggle/working/ace")
    sys.path.insert(0, "/kaggle/working/ace")
    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationConfig, GenerationParams, generate_music

    dit = AceStepHandler()
    dit.initialize_service(project_root="/kaggle/working/ace", config_path="acestep-v15-turbo", device="cuda")
    llm, thinking = None, False
    try:  # the LM planner is optional; T4 has room for the 0.6B one with the PyTorch backend
        from acestep.llm_inference import LLMHandler
        llm = LLMHandler()
        llm.initialize(checkpoint_dir="/kaggle/working/ace/checkpoints", lm_model_path="acestep-5Hz-lm-0.6B", backend="pt", device="cuda")
        thinking = True
    except Exception as e:  # noqa: BLE001
        print(f"LM planner unavailable, DiT only: {e}")
    setup_s = time.time() - t_setup

    n = int(m["candidates"])
    params = GenerationParams(caption=m["caption"], lyrics="[Instrumental]", instrumental=True, bpm=m.get("bpm"),
                              keyscale=m.get("keyscale") or "", duration=float(m["duration"]), thinking=thinking)
    config = GenerationConfig(batch_size=n, use_random_seed=False, seeds=[int(m["seed"]) + i for i in range(n)], audio_format="wav")
    t0 = time.time()
    result = generate_music(dit, llm, params, config, save_dir=f"{OUT}/music_raw")
    dt = time.time() - t0
    if not result.success:
        log({"kind": "music", "key": m["key"], "status": "error", "error": str(result.error), "seconds": round(dt, 1)})
        return
    for i, a in enumerate(result.audios):
        dst = f"{OUT}/cand{i}-{m['key']}.wav"
        os.replace(a["path"], dst)
        log({"kind": "music", "key": m["key"], "candidate": i, "seed": int(m["seed"]) + i, "status": "ok",
             "seconds": round(dt / max(len(result.audios), 1), 1), "setup_seconds": round(setup_s, 1),
             "thinking": thinking, "model": "acestep-v15-turbo", "file": os.path.basename(dst)})


# ------------------------------------------------------------------------ video
def run_video(job_dir):
    job = json.load(open(f"{job_dir}/job.json")).get("video", [])
    if not job:
        print("no video job")
        return
    pip("-U", "diffusers>=0.35.0", "transformers>=4.51", "accelerate", "ftfy", "imageio", "imageio-ffmpeg", "sentencepiece")
    import numpy as np
    import torch
    from diffusers import AutoencoderKLWan, WanImageToVideoPipeline
    from diffusers.utils import export_to_video, load_image

    dtype = torch.bfloat16 if os.environ.get("WAN_DTYPE") == "bf16" else torch.float16  # T4 has no fast bf16
    t_setup = time.time()
    model_id = job[0]["model"]
    vae = AutoencoderKLWan.from_pretrained(model_id, subfolder="vae", torch_dtype=torch.float32)
    pipe = WanImageToVideoPipeline.from_pretrained(model_id, vae=vae, torch_dtype=dtype)
    pipe.enable_model_cpu_offload()  # 5B transformer + umT5 text encoder do not fit 16 GB together
    try:
        pipe.vae.enable_tiling()
    except Exception:  # noqa: BLE001
        pass
    setup_s = time.time() - t_setup

    for j in job:
        dst = f"{OUT}/{j['shot']}-{j['key']}.mp4"
        if os.path.exists(dst):
            continue
        img = load_image(f"{job_dir}/{j['keyframe_name']}").convert("RGB").resize((j["width"], j["height"]))
        t0 = time.time()
        try:
            frames = pipe(image=img, prompt=j["prompt"], negative_prompt=j["negative_prompt"], height=j["height"],
                          width=j["width"], num_frames=j["frames"], num_inference_steps=j["steps"], guidance_scale=5.0,
                          generator=torch.Generator("cpu").manual_seed(int(j["seed"]))).frames[0]
        except torch.cuda.OutOfMemoryError as e:
            torch.cuda.empty_cache()
            log({"kind": "video", "shot": j["shot"], "key": j["key"], "status": "oom", "error": str(e)[:300],
                 "seconds": round(time.time() - t0, 1)})
            continue
        dt = time.time() - t0
        arr = np.asarray(frames[len(frames) // 2], dtype=np.float32)
        if arr.max() <= 1.0:
            arr = arr * 255
        mean = float(arr.mean())
        if mean < 3:  # fp16 overflow on T4 shows up as black frames
            log({"kind": "video", "shot": j["shot"], "key": j["key"], "status": "black_frames", "mean_pixel": mean, "seconds": round(dt, 1)})
            continue
        export_to_video(frames, dst, fps=j["fps"])
        log({"kind": "video", "shot": j["shot"], "key": j["key"], "status": "ok", "seconds": round(dt, 1),
             "setup_seconds": round(setup_s, 1), "model": model_id, "width": j["width"], "height": j["height"],
             "frames": j["frames"], "steps": j["steps"], "seed": j["seed"], "dtype": str(dtype), "mean_pixel": round(mean, 1),
             "file": os.path.basename(dst)})


if __name__ == "__main__":
    job_dir = find_job_dir()
    if len(sys.argv) > 1 and sys.argv[1] == "music":
        run_music(job_dir)
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "video":
        run_video(job_dir)
        sys.exit(0)
    print("GPU:", gpu_name())
    script = os.path.abspath(__file__)
    for part in ("music", "video"):  # separate processes: each frees its GPU memory on exit
        r = subprocess.run([sys.executable, script, part])
        if r.returncode != 0:
            log({"kind": part, "status": "process_failed", "returncode": r.returncode})
    print("done")
