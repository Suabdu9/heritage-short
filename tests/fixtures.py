"""Synthetic stand-ins for model outputs, so assembly can be tested offline with no keys.

Writes, inside a *copy* of the repo:
  consent.json (granted, test only), narration WAVs (tones whose length follows the line
  length), keyframes (labelled gradients) and the matching outputs/<stage>/current.json.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def make_copy(dst: Path) -> Path:
    for name in ("pipeline", "schema", "storyboard.json"):
        src = REPO / name
        if src.is_dir():
            shutil.copytree(src, dst / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy(src, dst / name)
    (dst / "outputs").mkdir()
    return dst


def grant_consent(root: Path) -> None:
    rec = {"granted": True, "scope": ["likeness", "story"], "date": "test", "method": "test fixture", "evidence": "test"}
    (root / "consent.json").write_text(json.dumps({"mother": rec, "father": rec}))


def fake_assets(root: Path) -> None:
    from PIL import Image, ImageDraw

    sb = json.loads((root / "storyboard.json").read_text(encoding="utf-8"))
    narr_dir = root / "outputs" / "narration"
    kf_dir = root / "outputs" / "keyframes"
    narr_dir.mkdir(parents=True)
    kf_dir.mkdir(parents=True)
    narr, kfs = {}, {}
    for i, s in enumerate(sb["shots"]):
        if s["narration"].strip():
            dur = max(1.5, len(s["narration"]) / 13)  # roughly Amharic TTS pace
            p = narr_dir / f"{s['id']}-fake.wav"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            f"sine=frequency={300 + 20 * i}:sample_rate=48000:duration={dur}", "-af",
                            "tremolo=f=5:d=0.7,volume=0.5", "-ac", "1", str(p)], check=True)
            narr[s["id"]] = {"file": f"outputs/narration/{p.name}", "duration_s": round(dur, 2), "provider": "fake"}
        else:
            narr[s["id"]] = {"file": None, "duration_s": 0.0}
        img = Image.new("RGB", (1024, 1024))
        d = ImageDraw.Draw(img)
        for y in range(1024):
            d.line([(0, y), (1024, y)], fill=(40 + y // 8, 30 + (i * 20) % 120, 90 + y // 12))
        d.ellipse([412, 412, 612, 612], outline=(255, 230, 180), width=8)
        d.text((470, 500), s["id"], fill=(255, 255, 255))
        p = kf_dir / f"{s['id']}-fake.png"
        img.save(p)
        kfs[s["id"]] = f"outputs/keyframes/{p.name}"
    (narr_dir / "current.json").write_text(json.dumps(narr, indent=2))
    (kf_dir / "current.json").write_text(json.dumps(kfs, indent=2))
