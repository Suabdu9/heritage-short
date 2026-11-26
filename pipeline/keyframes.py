"""Stage: reference sheets, then keyframes.

1. One reference sheet per character (front + three-quarter view, plain background),
   generated from the character's fixed description and the style block.
2. One keyframe per shot. With a FLUX.2 model the sheets of the characters in the shot
   are passed as reference images, so the same person comes back in every shot.
   FLUX.1 schnell (fallback) cannot take references; that run is logged as such.

Cache key = model + steps + size + expanded prompt + seed + hashes of the sheets used,
so editing one shot's prompt or seed regenerates exactly that keyframe, while editing a
character description regenerates its sheet and every shot it appears in.
"""
from __future__ import annotations

import time

from .common import (MissingKey, ProviderError, StageResult, cached, file_hash, log_event, rel, require_consent, say,
                     stable_hash, write_current, expand_prompt)
from .providers import PRICES, cloudflare_image

STAGE = "keyframes"
SHEET_STAGE = "reference"


def _img_cfg(sb):
    m = sb["models"]["image"]
    return m["model"], m.get("fallback_model"), m.get("steps", 4), m.get("width", 1024), m.get("height", 1024)


def sheet_prompt(sb: dict, char: dict) -> str:
    # ONE figure only: a sheet showing the person twice made FLUX.2 copy both figures
    # into every scene (two mothers in s02, two fathers in s03). See FAILURES.md F11.
    return (f"portrait of a single person, {char['description']}, standing alone, full body, facing the viewer, "
            f"neutral friendly expression, plain warm cream background, only one person in the image. "
            f"{sb['style']['block']}")


def _generate(stage, unit, model, fallback, prompt, seed, steps, w, h, refs, ref_keys, res, only, extra_log=None):
    """Try the primary model, then the fallback. Returns the output path or None."""
    for i, m in enumerate([model] + ([fallback] if fallback else [])):
        used_refs = refs if "flux-2" in m else []
        key = stable_hash({"model": m, "steps": steps, "w": w, "h": h, "prompt": prompt, "seed": seed,
                           "refs": ref_keys if used_refs else []})
        out = cached(stage, unit, key, "png")
        if out.exists():
            log_event(stage, shot=unit, provider="cloudflare", model=m, cache="hit", key=key, output=rel(out))
            say(f"  {unit}: cached ({m.split('/')[-1]})")
            res.cached += 1
            return out
        if only is not None and unit not in only:
            continue
        t0 = time.time()
        try:
            img, meta = cloudflare_image(m, prompt, seed=seed, steps=steps, width=w, height=h, reference_images=used_refs)
        except (ProviderError, MissingKey) as e:
            log_event(stage, shot=unit, provider="cloudflare", model=m, status="error", latency_s=round(time.time() - t0, 2),
                      seed=seed, error=str(e)[:400])
            say(f"  {unit}: {m} failed -> {e}")
            if isinstance(e, MissingKey):
                raise
            continue
        dt = time.time() - t0
        out.write_bytes(img)
        log_event(stage, shot=unit, provider="cloudflare", model=m, params={"steps": steps, "width": w, "height": h,
                  "reference_images": meta["reference_images"], "prompt": prompt}, seed=seed, latency_s=round(dt, 2),
                  cost_usd_list=PRICES["cloudflare_image_usd"], cost_usd_paid=0.0, status="ok", cache="miss", key=key,
                  output=rel(out), **(extra_log or {}))
        say(f"  {unit}: generated with {m.split('/')[-1]} ({meta['reference_images']} refs) in {dt:.1f}s")
        res.generated += 1
        if i > 0:
            res.fallback += 1
        return out
    if only is None or unit in only:
        res.failed += 1
    return None


def shot_prompt(sb: dict, shot: dict, ref_chars: list[str]) -> str:
    """Expanded prompt + which reference image is which person + what to avoid."""
    chars = {c["id"]: c for c in sb["characters"]}
    p = expand_prompt(sb, shot["image_prompt"])
    if ref_chars:
        who = "; ".join(f"image {i + 1} shows the {'woman' if c == 'mother' else 'man'} ({chars[c]['name']}), keep {'her' if c == 'mother' else 'his'} face and "
                        f"clothes exactly" for i, c in enumerate(ref_chars))
        p += f". Reference images: {who}. Use each reference person once only; do not swap clothes between them."
    if sb["style"].get("negative"):
        p += f" Avoid: {sb['style']['negative']}."
    return p


def run_sheets(sb: dict, only: set[str] | None = None) -> tuple[StageResult, dict]:
    model, fallback, steps, w, h = _img_cfg(sb)
    res = StageResult()
    sheets = {}
    for c in sb["characters"]:
        require_consent(sb, [c["id"]], "likeness")
        out = _generate(SHEET_STAGE, c["id"], model, fallback, sheet_prompt(sb, c), c.get("sheet_seed", 1), steps, w, h,
                        [], [], res, only)
        if out:
            sheets[c["id"]] = rel(out)
    write_current(SHEET_STAGE, sheets)
    return res, sheets


def run(sb: dict, only: set[str] | None = None) -> StageResult:
    from .common import ROOT
    say("reference sheets:")
    _, sheets = run_sheets(sb)  # cache hits unless a character description changed
    say("keyframes:")
    model, fallback, steps, w, h = _img_cfg(sb)
    res = StageResult()
    current = {}
    for shot in sb["shots"]:
        chars = shot.get("characters", [])
        require_consent(sb, chars, "likeness")
        ref_paths = [ROOT / sheets[c] for c in chars if c in sheets]
        refs = [p.read_bytes() for p in ref_paths]
        ref_keys = [file_hash(p) for p in ref_paths]
        out = _generate(STAGE, shot["id"], model, fallback, shot_prompt(sb, shot, [c for c in chars if c in sheets]), shot["seed"],
                        steps, w, h, refs, ref_keys, res, only)
        if out:
            current[shot["id"]] = rel(out)
    write_current(STAGE, current)
    return res


def explore(sb: dict, shot_id: str, n: int = 4) -> list[str]:
    """Generate n seed variations of one shot into outputs/keyframes/explore/ without
    touching the cache. Pick one, then put its seed into storyboard.json."""
    from .common import ROOT, stage_dir
    model, _, steps, w, h = _img_cfg(sb)
    shot = next(s for s in sb["shots"] if s["id"] == shot_id)
    require_consent(sb, shot.get("characters", []), "likeness")
    from .common import read_current
    sheets = {k: ROOT / v for k, v in read_current(SHEET_STAGE).items()}
    refs = [sheets[c].read_bytes() for c in shot.get("characters", []) if c in sheets]
    d = stage_dir(f"{STAGE}/explore")
    outs = []
    for k in range(n):
        seed = shot["seed"] + 1000 * (k + 1)
        t0 = time.time()
        img, meta = cloudflare_image(model, shot_prompt(sb, shot, [c for c in shot.get("characters", []) if c in sheets]), seed=seed, steps=steps, width=w, height=h,
                                     reference_images=refs)
        p = d / f"{shot_id}-seed{seed}.png"
        p.write_bytes(img)
        log_event(STAGE, shot=shot_id, provider="cloudflare", model=model, purpose="explore", seed=seed,
                  latency_s=round(time.time() - t0, 2), cost_usd_list=PRICES["cloudflare_image_usd"], cost_usd_paid=0.0,
                  status="ok", output=rel(p))
        outs.append(rel(p))
        say(f"  {shot_id}: seed {seed} -> {rel(p)}")
    return outs
