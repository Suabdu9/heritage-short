"""Stage: assembly (pure FFmpeg + Pillow, deterministic, no model calls).

    timeline   narration durations set shot lengths (picture is cut to the voice)
    segments   per shot and aspect: Wan clip (cropped, slowed <=1.5x, held) or a Ken
               Burns move over the keyframe; black frames in the radio cut. Cached by
               a hash of the source file and timing, so one changed shot re-renders one
               segment.
    audio      narration placed on the timeline -> score faded, gain-staged and
               side-chain ducked under the voice -> two-pass loudnorm to -14 LUFS,
               -1 dBTP
    subtitles  English SRT built from the same timeline, wrapped to the safe area
    end card   "Made with AI" + credits, drawn with Pillow
    export     16:9 and 9:16 H.264/AAC MP4, constant 24 fps, faststart
"""
from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from .common import (OUT, ROOT, file_hash, ffmpeg, log_event, need_ffmpeg, probe_duration, read_current, rel, say,
                     stable_hash, stage_dir)

STAGE = "assemble"
FPS = 24
SEG_VERSION = 3  # bump when segment rendering code changes, to invalidate old segments


def canvases(draft: bool) -> dict[str, tuple[int, int]]:
    return {"16x9": (1280, 720), "9x16": (720, 1280)} if draft else {"16x9": (1920, 1080), "9x16": (1080, 1920)}


# --------------------------------------------------------------------- timeline
def build_timeline(sb: dict) -> dict:
    narr = read_current("narration")
    n = sb["narration"]
    lead, gap, tail = n.get("lead_in_s", 1.0), n.get("gap_s", 0.4), n.get("tail_s", 0.8)
    t = 0.0
    shots = []
    last_voiced = max((i for i, s in enumerate(sb["shots"]) if s["narration"].strip()), default=-1)
    for i, s in enumerate(sb["shots"]):
        info = narr.get(s["id"], {})
        vdur = float(info.get("duration_s") or 0.0) if info.get("file") else 0.0
        pre = lead if i == 0 else 0.35
        need = pre + vdur + gap + (tail if i == last_voiced else 0)
        length = round(max(s["duration_s"], need if vdur else 0), 3)
        shots.append({"id": s["id"], "start": round(t, 3), "length": length,
                      "voice_file": info.get("file"), "voice_start": round(t + pre, 3), "voice_dur": round(vdur, 3),
                      "subtitle": s["subtitle_en"]})
        t += length
    end = sb["end_card"]["duration_s"]
    return {"shots": shots, "film_s": round(t, 3), "end_card_s": end, "total_s": round(t + end, 3)}


# -------------------------------------------------------------------- subtitles
def _fmt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def _chunks(text: str, width: int) -> list[str]:
    """Split into cues of at most two lines of `width` characters, breaking at
    sentence or clause boundaries where possible."""
    lines = textwrap.wrap(text, width=width)
    if len(lines) <= 2:
        return ["\n".join(lines)]
    parts = re.split(r"(?<=[.:;,!?])\s+", text)
    cues, cur = [], ""
    for p in parts:
        cand = (cur + " " + p).strip()
        if len(textwrap.wrap(cand, width=width)) <= 2:
            cur = cand
        else:
            if cur:
                cues.append(cur)
            cur = p
    if cur:
        cues.append(cur)
    out = []
    for c in cues:
        w = textwrap.wrap(c, width=width)
        for k in range(0, len(w), 2):
            out.append("\n".join(w[k:k + 2]))
    return out


def write_srt(tl: dict, path: Path, width: int) -> int:
    items, idx = [], 1
    for s in tl["shots"]:
        if not s["subtitle"] or not s["voice_dur"]:
            continue
        cues = _chunks(s["subtitle"], width)
        total_chars = sum(len(c) for c in cues)
        t = s["voice_start"]
        span = s["voice_dur"] + 0.3
        for c in cues:
            d = span * len(c) / total_chars
            items.append(f"{idx}\n{_fmt(t)} --> {_fmt(t + d - 0.05)}\n{c}\n")
            idx += 1
            t += d
    path.write_text("\n".join(items), encoding="utf-8")
    return idx - 1


# -------------------------------------------------------------------- segments
def _crop_expr(aspect: str) -> str:
    # centre-crop any input to the target aspect ratio (keyframes are square, subject centred)
    return ("crop='min(iw,ih*16/9)':'min(ih,iw*9/16)'" if aspect == "16x9"
            else "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)'")


def _kenburns_filter(kb: dict, w: int, h: int, frames: int) -> str:
    z0, z1 = kb.get("zoom_from", 1.0), kb.get("zoom_to", 1.1)
    pan = kb.get("pan", "none")
    if pan != "none" and max(z0, z1) < 1.06:
        z0, z1 = z0 + 0.08, z1 + 0.08
    p = f"(on/{max(frames - 1, 1)})"
    z = f"{z0}+({z1}-{z0})*{p}"
    cx, cy = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    x = {"left": f"(iw-iw/zoom)*(1-{p})", "right": f"(iw-iw/zoom)*{p}"}.get(pan, cx)
    y = {"up": f"(ih-ih/zoom)*(1-{p})", "down": f"(ih-ih/zoom)*{p}"}.get(pan, cy)
    # upscale first: zoompan rounds to whole pixels, which jitters on small inputs
    return f"scale={w * 2}:{h * 2}:flags=lanczos,zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS}"


def render_segment(shot: dict, length: float, source: dict, aspect: str, size: tuple[int, int], radio: bool) -> Path:
    w, h = size
    frames = int(round(length * FPS))
    fade = 0.35
    if radio or source.get("type") in (None, "none"):
        src_desc = {"type": "black"}
    elif source["type"] == "clip":
        src_desc = {"type": "clip", "hash": file_hash(ROOT / source["file"])}
    else:
        src_desc = {"type": "kenburns", "hash": file_hash(ROOT / source["keyframe"]),
                    "kb": {k: source.get(k) for k in ("zoom_from", "zoom_to", "pan")}}
    key = stable_hash({"v": SEG_VERSION, "len": length, "aspect": aspect, "size": size, **src_desc})
    out = stage_dir(f"{STAGE}/segments") / f"{shot['id']}-{aspect}-{key}.mp4"
    if out.exists():
        log_event(STAGE, shot=shot["id"], provider="ffmpeg", what=f"segment {aspect}", cache="hit", key=key)
        return out
    tail = f",fade=t=in:st=0:d={fade},fade=t=out:st={max(length - fade, 0):.3f}:d={fade},format=yuv420p"
    enc = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS), "-an"]
    if src_desc["type"] == "black":
        ffmpeg(["-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={FPS}:d={length}", *enc, "-t", f"{length}", str(out)],
               what=f"segment {shot['id']} {aspect} black")
    elif src_desc["type"] == "clip":
        clip = ROOT / source["file"]
        cdur = probe_duration(clip) - 0.15  # drop the weak last frames
        slow = min(max(length / cdur, 1.0), 1.5)
        vf = (f"trim=0:{cdur:.3f},setpts={slow:.4f}*(PTS-STARTPTS),{_crop_expr(aspect)},scale={w}:{h}:flags=lanczos,"
              f"fps={FPS},tpad=stop_mode=clone:stop_duration={length:.3f},trim=0:{length:.3f},setpts=PTS-STARTPTS{tail}")
        ffmpeg(["-i", str(clip), "-vf", vf, *enc, str(out)], what=f"segment {shot['id']} {aspect} clip x{slow:.2f}")
    else:
        kf = ROOT / source["keyframe"]
        vf = f"{_crop_expr(aspect)},{_kenburns_filter(source, w, h, frames)},trim=end_frame={frames}{tail}"
        ffmpeg(["-loop", "1", "-i", str(kf), "-vf", vf, "-frames:v", str(frames), *enc, str(out)],
               what=f"segment {shot['id']} {aspect} kenburns")
    return out


# --------------------------------------------------------------------- end card
def _font(names: list[str], size: int):
    from PIL import ImageFont
    dirs = [Path("C:/Windows/Fonts"), Path("/usr/share/fonts"), Path("/Library/Fonts"), Path("/System/Library/Fonts"),
            Path.home() / ".fonts", ROOT / "assets" / "fonts"]
    for d in dirs:
        if not d.exists():
            continue
        for n in names:
            hits = list(d.rglob(n))
            if hits:
                return ImageFont.truetype(str(hits[0]), size)
    return None


def render_end_card(sb: dict, aspect: str, size: tuple[int, int]) -> Path:
    from PIL import Image, ImageDraw
    w, h = size
    ec = sb["end_card"]
    key = stable_hash({"ec": ec, "title": sb.get("title_native", ""), "size": size, "v": 2})
    out = stage_dir(f"{STAGE}/segments") / f"endcard-{aspect}-{key}.mp4"
    if out.exists():
        return out
    img = Image.new("RGB", (w, h), (24, 20, 38))
    d = ImageDraw.Draw(img)
    unit = min(w, h)
    big = _font(["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "segoeuib.ttf", "NotoSans-Bold.ttf"], int(unit * 0.085))
    small = _font(["DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "segoeui.ttf", "NotoSans-Regular.ttf"], int(unit * 0.032))
    eth = _font(["NotoSansEthiopic-Regular.ttf", "NotoSansEthiopic*.ttf", "nyala.ttf", "ebrima.ttf", "AbyssinicaSIL-Regular.ttf", "Kefa.ttc"], int(unit * 0.05))
    if big is None or small is None:
        from PIL import ImageFont
        big = small = ImageFont.load_default()
    y = h * 0.36
    if eth and sb.get("title_native"):
        tw = d.textlength(sb["title_native"], font=eth)
        d.text(((w - tw) / 2, y - unit * 0.12), sb["title_native"], font=eth, fill=(235, 200, 130))
    tw = d.textlength(ec["text"], font=big)
    d.text(((w - tw) / 2, y), ec["text"], font=big, fill=(250, 246, 235))
    y += unit * 0.14
    for line in ec.get("subtext", "").split("\n"):
        for sub in textwrap.wrap(line, width=48 if aspect == "16x9" else 34):
            tw = d.textlength(sub, font=small)
            d.text(((w - tw) / 2, y), sub, font=small, fill=(205, 198, 215))
            y += unit * 0.05
    png = out.with_suffix(".png")
    img.save(png)
    dur = ec["duration_s"]
    ffmpeg(["-loop", "1", "-i", str(png), "-vf", f"fade=t=in:st=0:d=0.5,format=yuv420p", "-t", f"{dur}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", str(out)], what=f"end card {aspect}")
    return out


# ------------------------------------------------------------------------ audio
def _loudnorm_json(log: str) -> dict:
    """Pull loudnorm's JSON block out of the ffmpeg log (newer builds print more after it)."""
    i = log.rfind('"input_i"')
    start = log.rfind("{", 0, i)
    obj, _ = json.JSONDecoder().raw_decode(log[start:])
    return obj


def build_audio(sb: dict, tl: dict, build: Path, music_file: Path) -> tuple[Path, dict]:
    total = tl["total_s"]
    voiced = [s for s in tl["shots"] if s["voice_file"]]
    # 1) narration on the timeline, normalised as one stem
    inputs, labels, filters = [], [], []
    for i, s in enumerate(voiced):
        inputs += ["-i", str(ROOT / s["voice_file"])]
        ms = int(s["voice_start"] * 1000)
        filters.append(f"[{i}:a]aresample=48000,adelay={ms}|{ms}[v{i}]")
        labels.append(f"[v{i}]")
    voice = build / "voice.wav"
    if voiced:
        graph = ";".join(filters) + f";{''.join(labels)}amix=inputs={len(voiced)}:normalize=0,apad=whole_dur={total},atrim=0:{total},loudnorm=I=-16:TP=-2:LRA=11[v]"
        ffmpeg([*inputs, "-filter_complex", graph, "-map", "[v]", "-ar", "48000", "-ac", "1", str(voice)], what="voice stem")
    else:
        ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono", "-t", f"{total}", str(voice)], what="silent voice stem")

    # 2) score: trim/pad to length, fades, gain-stage, then duck under the voice
    duck = {"threshold": 0.02, "ratio": 6, "attack_ms": 300, "release_ms": 1500, "music_gain_db": -6, **sb["music"].get("duck", {})}
    fade_out = max(total - 3.5, 0)
    graph = (f"[0:a]aresample=48000,aformat=channel_layouts=stereo,apad=whole_dur={total},atrim=0:{total},"
             f"loudnorm=I=-20:TP=-2:LRA=11,volume={duck['music_gain_db']}dB,afade=t=in:d=2,afade=t=out:st={fade_out:.2f}:d=3.5[m];"
             f"[1:a]aformat=channel_layouts=stereo,asplit=2[sc][vo];"
             f"[m][sc]sidechaincompress=threshold={duck['threshold']}:ratio={duck['ratio']}:attack={duck['attack_ms']}:"
             f"release={duck['release_ms']}:makeup=1[md];"
             f"[vo][md]amix=inputs=2:normalize=0:duration=first[mix]")
    premix = build / "premix.wav"
    ffmpeg(["-i", str(music_file), "-i", str(voice), "-filter_complex", graph, "-map", "[mix]", "-ar", "48000", str(premix)],
           what="duck + mix")

    # 3) two-pass loudness normalisation to the platform reference
    err = ffmpeg(["-i", str(premix), "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"],
                 what="loudnorm pass 1", log=False, loglevel="info")
    m = _loudnorm_json(err)
    final = build / "final_audio.wav"
    err2 = ffmpeg(["-i", str(premix), "-af",
                   f"loudnorm=I=-14:TP=-1:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
                   f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:"
                   f"linear=true:print_format=json", "-ar", "48000", str(final)], what="loudnorm pass 2", loglevel="info")
    m2 = _loudnorm_json(err2)
    loud = {"input_lufs": float(m["input_i"]), "output_lufs": float(m2["output_i"]), "output_true_peak": float(m2["output_tp"]),
            "normalization": m2.get("normalization_type")}
    return final, loud


# ------------------------------------------------------------------------- main
def run(sb: dict, radio: bool = False) -> list[Path]:
    need_ffmpeg()
    draft = sb.get("draft", True)
    tl = build_timeline(sb)
    build = stage_dir(f"{STAGE}/build")
    (build / "timeline.json").write_text(json.dumps(tl, indent=2, ensure_ascii=False), encoding="utf-8")
    say(f"  timeline: {len(tl['shots'])} shots, film {tl['film_s']:.1f}s + end card {tl['end_card_s']}s = {tl['total_s']:.1f}s")
    if not 60 <= tl["total_s"] <= 90:
        say(f"  WARNING: total {tl['total_s']:.1f}s is outside the 60-90 s brief; adjust shot durations in storyboard.json")

    mus = read_current("music")
    music_file = ROOT / mus["file"] if mus.get("file") else None
    if not music_file or not music_file.exists():
        from .music import placeholder
        music_file = placeholder(sb["music"]["duration_s"])
    audio, loud = build_audio(sb, tl, build, music_file)
    say(f"  audio: {loud['output_lufs']:.1f} LUFS, true peak {loud['output_true_peak']:.1f} dBTP ({loud['normalization']})")

    sources = read_current("clips")
    if not radio and not sources:  # shots stage not run: fall back to keyframes directly
        kfs = read_current("keyframes")
        sources = {sid: {"type": "kenburns", "keyframe": kf, **next(s for s in sb["shots"] if s["id"] == sid).get("kenburns", {})}
                   for sid, kf in kfs.items()}
    outs = []
    aspects = canvases(draft)
    if radio:  # the listening test happens on phones (WhatsApp), so the radio cut is vertical
        aspects = {"9x16": aspects["9x16"]}
    for aspect, size in aspects.items():
        segs = []
        for s, plan in zip(sb["shots"], tl["shots"]):
            segs.append(render_segment(s, plan["length"], sources.get(s["id"], {}), aspect, size, radio))
        segs.append(render_end_card(sb, aspect, size))
        lst = build / f"concat-{aspect}.txt"
        lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segs), encoding="utf-8")
        silent = build / f"picture-{aspect}.mp4"
        ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent)], what=f"concat {aspect}")

        srt = build / f"subs-{aspect}.srt"
        n = write_srt(tl, srt, 42 if aspect == "16x9" else 30)
        style = ("FontName=DejaVu Sans,FontSize=15,Outline=1.5,Shadow=0.5,MarginV=16,BorderStyle=1" if aspect == "16x9"
                 else "FontName=DejaVu Sans,FontSize=10,Outline=1.2,Shadow=0.5,MarginV=62,MarginL=14,MarginR=14,BorderStyle=1")
        name = ("radio_cut" if radio else "heritage") + f"_{aspect}.mp4"
        final_dir = stage_dir("radio" if radio else "final")
        out = final_dir / name
        ffmpeg(["-i", silent.name, "-i", audio.name,
                "-vf", f"subtitles={srt.name}:charenc=UTF-8:force_style='{style}'",
                "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-r", str(FPS),
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                "-metadata", "comment=Made with AI (FLUX, Wan 2.2, ACE-Step, Addis AI TTS). See ETHICS.md",
                "-shortest", str(out.resolve())], cwd=build, what=f"export {aspect}")
        dur = probe_duration(out)
        log_event(STAGE, provider="ffmpeg", what=f"final {aspect}", status="ok", output=rel(out), duration_s=round(dur, 2),
                  subtitles=n, radio=radio, loudness=loud, draft=draft)
        say(f"  -> {rel(out)}  ({dur:.1f}s, {n} subtitle cues)")
        outs.append(out)
    return outs
