# Architecture

## Principle

`storyboard.json` is the only contract between stages. A human owns it; code only reads it.
Every stage is a function `run(storyboard) -> files in outputs/<stage>/`, and every stage can
be run on its own (`python -m pipeline <stage>`).

```
                 schema/storyboard.schema.json
                              │ validate (+ semantic checks)
                              ▼
                       storyboard.json ─────────────── consent.json (gate)
          ┌──────────────┬────┴─────────┬──────────────────┐
          ▼              ▼              ▼                  ▼
     narration      reference       music (plan)        script check
   Addis → Gemini     sheets          │                 (Gemini back-
   1 WAV / shot         │             │ job.json          translation)
          │             ▼             ▼
          │         keyframes      Kaggle T4: ACE-Step 1.5 ──┐
          │      FLUX.2 klein +       │                      │ kaggle-pull
          │      sheets as refs       ▼                      ▼
          │             │        shots (plan) ─job.json─► Kaggle T4: Wan 2.2 TI2V-5B
          │             │             │ Ken Burns fallback     │
          ▼             ▼             ▼                        ▼
      ┌──────────────────────── assemble (FFmpeg) ──────────────────────────┐
      │ timeline ← narration durations · segments (clip | Ken Burns | black) │
      │ voice stem · score → sidechain duck → 2-pass loudnorm −14 LUFS/−1 dBTP│
      │ SRT from timeline → burned in · end card (Pillow) · 16:9 + 9:16       │
      └──────────────────────────────────────────────────────────────────────┘
                          every call / hit / render → runs.jsonl
```

## Stages and contracts

| Stage | Reads from storyboard | Reads from other stages | Writes |
|---|---|---|---|
| validate | everything | – | nothing (fails loudly) |
| script | `shots[].narration`, `subtitle_en`, `models.text` | – | `outputs/script/check.json` |
| narration | `shots[].narration`, `models.tts`, `language` | – | `outputs/narration/<shot>-<key>.wav`, `current.json` (file + measured duration) |
| reference | `characters[]`, `style`, `models.image` | – | `outputs/reference/<char>-<key>.png` |
| keyframes | `shots[].image_prompt`, `seed`, `characters`, `style` | reference sheets | `outputs/keyframes/<shot>-<key>.png` |
| shots | `shots[].motion_prompt`, `seed`, `kenburns`, `models.video`, `draft` | keyframes | `outputs/clips/current.json`, `job.json`; clips arrive as `outputs/clips/<shot>-<key>.mp4` |
| music | `music.*`, `models.music` | – | `outputs/music/job.json`; candidates arrive as `cand<i>-<key>.wav`; temp score `placeholder-*.wav` |
| assemble | `shots[].duration_s`, `subtitle_en`, `narration` timing, `music.duck`, `end_card`, `draft` | all of the above via `current.json` | `outputs/final/heritage_{16x9,9x16}.mp4`, `outputs/radio/radio_cut_9x16.mp4` |

The schema ([schema/storyboard.schema.json](schema/storyboard.schema.json)) enforces 8–12
shots, a stated `reason` for every shot, durations, seeds, and `consent_id` for every real
person. `common._semantic_checks` adds what JSON Schema cannot express: unique shot ids,
`{character}` placeholders that exist and are listed on the shot, narration and subtitle
both present or both absent, music cues that point at real shots, and a planned length
near 60–90 s.

## Caching

Every unit of work gets a key: the SHA-256 of exactly the inputs that change its output.
The output file name contains the key, so "is it cached?" is "does the file exist?".

| Unit | Key inputs |
|---|---|
| narration line | text, provider, voice, language, style prompt, speed |
| reference sheet | model, steps, size, sheet prompt (character description + style), seed |
| keyframe | model, steps, size, expanded prompt, seed, **hashes of the reference sheets used** |
| Wan clip | **keyframe file hash**, motion prompt, seed, model, size, steps, frames |
| score | caption, bpm, key, duration, seed, model, number of candidates |
| video segment | source file hash, Ken Burns params, shot length, aspect, canvas, renderer version |

Consequences, which the tests check:

- Editing one narration line changes one key → one TTS call → one segment re-render
  (shot lengths follow the voice).
- Editing one image prompt → one keyframe → its Wan clip key changes because the keyframe
  hash changed → one clip queued for Kaggle.
- Editing a character description → that character's sheet → every shot they appear in.
- `tests/test_offline_e2e.py` changes one shot and asserts that exactly that shot's two
  segments are re-rendered.

`current.json` in each stage folder records which file each unit currently uses. It is
there for people and for the Kaggle job. Downstream stages recompute keys and do not trust it.

## Failure handling

| Failure | Handling |
|---|---|
| 429 / 5xx / timeout | `with_retries`: 3 attempts, exponential backoff (4 s, 8 s); 4xx fails at once |
| Provider down or out of quota | fallback chain: Addis → Gemini TTS; FLUX.2 klein → FLUX.1 schnell (logged as `fallback`) |
| Model renamed or retired | Gemini model names are resolved by listing models and picking the closest match; the model actually used is logged |
| No GPU / Kaggle quota gone | shots fall back to a Ken Burns move over the keyframe; the film still renders |
| No score yet | deterministic FFmpeg-synthesised temp score (D-major pad) |
| Kaggle session times out | the kernel writes each clip as soon as it is done; `kaggle-pull` takes whatever finished |
| Wan fp16 black frames on T4 | the kernel checks mean pixel value and marks the clip `black_frames` instead of shipping it; `WAN_DTYPE=bf16` switches precision |
| GPU out of memory | caught per clip and logged as `oom`; other clips continue |
| Missing key | the stage stops with the variable name (exit 4), before any partial output |
| Real person without consent | `ConsentError` before any call (exit 3) |
| Narration longer than planned | shot is stretched to fit the voice; total length is checked against 60–90 s |

## Logging

`runs.jsonl` has one JSON line per model call, cache hit, ffmpeg render and Kaggle GPU job:
`ts, run_id, stage, shot, provider, model, params, seed, latency_s, cost_usd_list,
cost_usd_paid, status, cache, key, output, error`, plus provider-specific fields (Addis
credits used, GPU name and seconds, measured loudness). `python -m pipeline costs`
aggregates it into the table in COSTS.md.

## Why this shape

- **Narration first.** Shot lengths come from measured speech, not guesses, because
  Amharic TTS speed is not known in advance and editors cut picture to the voice.
- **GPU work is a planned batch, not a live call.** Wan on a T4 takes minutes per clip and
  Kaggle sessions are separate machines. So the local pipeline only plans which clips are
  missing (`job.json`), and the GPU runs that batch. Re-runs never touch the GPU for
  unchanged shots.
- **Square keyframes, centred subjects.** One square image is centre-cropped to both 16:9
  and 9:16, so both exports come from the same material.
- **Everything after generation is deterministic.** FFmpeg + Pillow: same inputs, same film.

## Repository map

```
storyboard.json          the contract
schema/                  JSON Schema for it
consent.json             machine-readable consent (gate); human record in ETHICS.md
pipeline/common.py       env, validation, consent gate, hashing, logging, retries, ffmpeg
pipeline/providers.py    Gemini, Cloudflare, Addis clients
pipeline/{script,narration,keyframes,shots,music,assemble}.py   the stages
pipeline/kaggle_jobs.py  push / status / pull of the GPU batch
kaggle/gpu_jobs.py       runs on Kaggle: ACE-Step 1.5, then Wan 2.2
pipeline/costs.py        runs.jsonl → cost table
tests/                   unit tests + offline end-to-end render
```
