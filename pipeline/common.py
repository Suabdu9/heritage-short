"""Shared plumbing for every stage: paths, env, storyboard loading and validation,
consent gate, content-hash caching, run logging and retries.

Design rule: storyboard.json is the only input contract. A stage reads it, computes a
cache key for each unit of work from exactly the inputs that affect the output, and
writes `outputs/<stage>/<unit>-<key>.<ext>`. If that file already exists the work is
skipped. Changing one shot therefore regenerates only that shot.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
RUNS = ROOT / "runs.jsonl"
SCHEMA = ROOT / "schema" / "storyboard.schema.json"
CONSENT = ROOT / "consent.json"
RUN_ID = os.environ.get("RUN_ID") or time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


# --------------------------------------------------------------------------- env
def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Real env vars win. Never logged."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def need_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise MissingKey(f"missing {', '.join(missing)} (copy .env.example to .env and fill it in)")
    return {n: os.environ[n] for n in names}


class MissingKey(RuntimeError):
    pass


class ProviderError(RuntimeError):
    def __init__(self, msg: str, retryable: bool = False, status: int | None = None):
        super().__init__(msg)
        self.retryable = retryable
        self.status = status


# ---------------------------------------------------------------- storyboard
class StoryboardError(ValueError):
    pass


def load_storyboard(path: Path | str = ROOT / "storyboard.json") -> dict:
    import jsonschema

    path = Path(path)
    try:
        sb = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise StoryboardError(f"{path.name} is not valid JSON: {e}") from e
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(sb), key=lambda e: list(e.path))
    if errors:
        lines = [f"  - {'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:20]]
        raise StoryboardError("storyboard.json failed schema validation:\n" + "\n".join(lines))
    _semantic_checks(sb)
    return sb


def _semantic_checks(sb: dict) -> None:
    """Rules JSON Schema cannot express."""
    problems = []
    ids = [s["id"] for s in sb["shots"]]
    if len(ids) != len(set(ids)):
        problems.append("shot ids must be unique")
    chars = {c["id"] for c in sb["characters"]}
    for s in sb["shots"]:
        for c in s.get("characters", []):
            if c not in chars:
                problems.append(f"{s['id']}: unknown character '{c}'")
        for ref in re.findall(r"{(\w+)}", s["image_prompt"]):
            if ref not in chars:
                problems.append(f"{s['id']}: image_prompt references unknown character '{{{ref}}}'")
            elif ref not in s.get("characters", []):
                problems.append(f"{s['id']}: image_prompt uses {{{ref}}} but 'characters' does not list it")
        if bool(s["narration"].strip()) != bool(s["subtitle_en"].strip()):
            problems.append(f"{s['id']}: narration and subtitle_en must both be set or both be empty")
    for cue in sb["music"]["cues"]:
        if cue["at_shot"] not in ids:
            problems.append(f"music cue points at unknown shot {cue['at_shot']}")
    planned = sum(s["duration_s"] for s in sb["shots"]) + sb["end_card"]["duration_s"]
    if not 50 <= planned <= 95:
        problems.append(f"planned length {planned:.1f}s is far outside the 60-90s target")
    if problems:
        raise StoryboardError("storyboard.json semantic check failed:\n" + "\n".join("  - " + p for p in problems))


def shots_by_id(sb: dict) -> dict[str, dict]:
    return {s["id"]: s for s in sb["shots"]}


def characters_by_id(sb: dict) -> dict[str, dict]:
    return {c["id"]: c for c in sb["characters"]}


def expand_prompt(sb: dict, text: str) -> str:
    """Replace {character} with its fixed description and append the style block.
    Re-using exactly the same words in every prompt is what keeps drift down."""
    chars = characters_by_id(sb)
    body = re.sub(r"{(\w+)}", lambda m: chars[m.group(1)]["description"], text)
    return f"{body}. {sb['style']['block']}"


# -------------------------------------------------------------------- consent
def require_consent(sb: dict, character_ids: Iterable[str], scope: str) -> None:
    """Refuse to generate anything showing or voicing a real person without a
    recorded consent entry covering this scope (likeness | story | voice)."""
    chars = characters_by_id(sb)
    real = [chars[c] for c in character_ids if chars[c]["real_person"]]
    if not real:
        return
    records = json.loads(CONSENT.read_text(encoding="utf-8")) if CONSENT.exists() else {}
    for c in real:
        rec = records.get(c["consent_id"], {})
        if not (rec.get("granted") is True and scope in rec.get("scope", [])):
            raise ConsentError(
                f"no recorded consent for '{c['consent_id']}' with scope '{scope}'. "
                f"Ask them, then record it in consent.json and ETHICS.md before generating."
            )


class ConsentError(RuntimeError):
    pass


# ---------------------------------------------------------------------- cache
def stable_hash(obj: Any, n: int = 12) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:n]


def file_hash(path: Path, n: int = 12) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


def stage_dir(stage: str) -> Path:
    d = OUT / stage
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached(stage: str, unit: str, key: str, ext: str) -> Path:
    return stage_dir(stage) / f"{unit}-{key}.{ext}"


def write_current(stage: str, mapping: dict[str, Any]) -> None:
    """outputs/<stage>/current.json: which file each unit currently uses (for humans
    and for the Kaggle jobs). Downstream stages recompute keys, they do not trust it."""
    p = stage_dir(stage) / "current.json"
    p.write_text(json.dumps(mapping, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_current(stage: str) -> dict:
    p = OUT / stage / "current.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


# -------------------------------------------------------------------- logging
def log_event(stage: str, **fields: Any) -> dict:
    """Append one JSON line to runs.jsonl. Called for every model call, every cache
    hit and every ffmpeg render, so costs and latency can be computed afterwards."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "run_id": RUN_ID, "stage": stage, **fields}
    with open(RUNS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def say(msg: str) -> None:
    print(msg, flush=True)


# -------------------------------------------------------------------- retries
def with_retries(fn: Callable[[], Any], *, attempts: int = 3, base_delay: float = 4.0, what: str = "call") -> Any:
    """Retry transient failures (429, 5xx, timeouts) with exponential backoff.
    Non-retryable errors (bad request, auth, safety block) raise immediately."""
    import requests

    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except ProviderError as e:
            last = e
            if not e.retryable:
                raise
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
        if i < attempts:
            delay = base_delay * (2 ** (i - 1))
            say(f"    {what}: attempt {i} failed ({last}); retrying in {delay:.0f}s")
            time.sleep(delay)
    raise last  # type: ignore[misc]


def check_response(r, provider: str) -> None:
    if r.status_code < 400:
        return
    retryable = r.status_code == 429 or r.status_code >= 500
    body = r.text[:400].replace("\n", " ")
    raise ProviderError(f"{provider} HTTP {r.status_code}: {body}", retryable=retryable, status=r.status_code)


# --------------------------------------------------------------------- ffmpeg
def FFMPEG() -> str:
    """ffmpeg from $FFMPEG, PATH, or the pip package imageio-ffmpeg (no admin rights needed)."""
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return ""


def need_ffmpeg() -> None:
    if not FFMPEG():
        raise RuntimeError("ffmpeg not found. Windows: winget install --id Gyan.FFmpeg -e --source winget ; "
                           "or: pip install imageio-ffmpeg ; macOS: brew install ffmpeg ; Linux: apt install ffmpeg")


def ffmpeg(args: list[str], *, cwd: Path | None = None, stage: str = "assemble", what: str = "", log: bool = True,
           loglevel: str = "error") -> str:
    need_ffmpeg()
    cmd = [FFMPEG(), "-hide_banner", "-nostats", "-loglevel", loglevel, "-y", *args]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    dt = time.time() - t0
    if p.returncode != 0:
        if log:
            log_event(stage, provider="ffmpeg", what=what, status="error", latency_s=round(dt, 2), error=p.stderr[-600:])
        raise RuntimeError(f"ffmpeg failed ({what}):\n{p.stderr[-1500:]}")
    if log:
        log_event(stage, provider="ffmpeg", what=what, status="ok", latency_s=round(dt, 2), cost_usd=0.0)
    return p.stderr


def probe_duration(path: Path) -> float:
    probe = shutil.which("ffprobe")
    if probe:
        p = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                           capture_output=True, text=True)
        try:
            return float(p.stdout.strip())
        except ValueError:
            pass
    # no ffprobe (e.g. imageio-ffmpeg): decode once and read the last time= stamp
    p = subprocess.run([FFMPEG(), "-hide_banner", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    stamps = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
    if not stamps:
        m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", p.stderr)
        if not m:
            raise RuntimeError(f"could not read duration of {path}: {p.stderr[-300:]}")
        stamps = [m.groups()]
    h, mi, se = stamps[-1]
    return int(h) * 3600 + int(mi) * 60 + float(se)


@dataclass
class StageResult:
    generated: int = 0
    cached: int = 0
    failed: int = 0
    fallback: int = 0

    def __str__(self) -> str:
        return f"generated={self.generated} cached={self.cached} fallback={self.fallback} failed={self.failed}"


def fail(msg: str, code: int = 2) -> None:
    say(f"ERROR: {msg}")
    sys.exit(code)
