"""End-to-end assembly with synthetic assets in a temp copy of the repo (no keys, ~1 min).
Checks both exports exist with the right size and length, and that a second run is
served entirely from the segment cache except for the one shot we change."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from tests.fixtures import fake_assets, grant_consent, make_copy

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def run(root, *args):
    p = subprocess.run([sys.executable, "-m", "pipeline", *args], cwd=root, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    j = json.loads(out)
    return j["streams"][0]["width"], j["streams"][0]["height"], float(j["format"]["duration"])


def test_film_offline(tmp_path):
    root = make_copy(tmp_path / "repo")
    grant_consent(root)
    fake_assets(root)
    run(root, "shots")
    run(root, "music")
    out = run(root, "assemble")
    assert "LUFS" in out
    w, h, d = probe(root / "outputs/final/heritage_16x9.mp4")
    assert w > h and 55 <= d <= 95
    w, h, d2 = probe(root / "outputs/final/heritage_9x16.mp4")
    assert h > w and abs(d - d2) < 0.2

    # change one shot's Ken Burns move -> only that shot's segments re-render
    sb = json.loads((root / "storyboard.json").read_text(encoding="utf-8"))
    sb["shots"][3]["kenburns"]["pan"] = "right"
    (root / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    run(root, "shots")
    run(root, "assemble")
    lines = [json.loads(l) for l in (root / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    last_run = lines[-1]["run_id"]
    rendered = {l["what"] for l in lines if l["run_id"] == last_run and l.get("what", "").startswith("segment") and l.get("cache") != "hit"}
    assert rendered == {"segment s04 16x9 kenburns", "segment s04 9x16 kenburns"}, rendered
